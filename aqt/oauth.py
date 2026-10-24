"""
OAuth2 for the "OAuth" SCA approach: PKCE, bank quirks, the client calls (discovery, code exchange, refresh),
the local redirect-URI listener, and the headless authorizer used by --sca-auto.
"""

import hashlib
import html
import http.client
import http.cookies
import ipaddress
import re
import secrets
import select
import socket
import ssl
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit

from . import jsonpath
from .errors import ConfigurationError, OperationCancelled
from .httpclient import HttpClient, TransportError, prepare_url
from .results import HttpExchange
from .signing import b64url_encode

PKCE_METHOD = "S256"
_WELL_KNOWN_OAUTH = "/.well-known/oauth-authorization-server"


# ---- PKCE (RFC 7636) -------------------------------------------------------------------------------------------

def create_verifier() -> str:
    return b64url_encode(secrets.token_bytes(32))


def create_challenge(verifier: str) -> str:
    return b64url_encode(hashlib.sha256(verifier.encode("ascii")).digest())


def create_state(prefix: str) -> str:
    """Random value for the OAuth state parameter."""
    return prefix + uuid.uuid4().hex


# ---- Quirks ----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class OAuthQuirks:
    """
    Bank-specific OAuth deviations, from the profile's quirks.oauth:
        quirks:
          oauth:
            scopeSuffix: " openid"     # appended to the scope
            nonce: "1234"              # adds nonce=...
            uiLocales: ka-ge           # adds ui_locales=...
            extraParams: { prompt: login }
    """
    scope_suffix: str | None = None
    nonce: str | None = None
    ui_locales: str | None = None
    extra_params: dict[str, str] = field(default_factory=dict)

    @staticmethod
    def from_profile(quirks) -> "OAuthQuirks":
        oauth = quirks.get("oauth") if isinstance(quirks, dict) else None
        if not isinstance(oauth, dict):
            return OAuthQuirks()

        def text(value):
            return None if value is None else value if isinstance(value, str) else jsonpath.as_text(value)

        extra = {}
        if isinstance(oauth.get("extraParams"), dict):
            extra = {k: text(v) for k, v in oauth["extraParams"].items() if v is not None}

        return OAuthQuirks(text(oauth.get("scopeSuffix")), text(oauth.get("nonce")), text(oauth.get("uiLocales")), extra)


# ---- Client ----------------------------------------------------------------------------------------------------

@dataclass
class OAuthCallResult:
    """Result of one call to the bank's auth server, recorded like any other exchange."""
    exchange: HttpExchange
    json: object = None

    @property
    def is_success(self) -> bool:
        return self.exchange.status_code is not None and 200 <= self.exchange.status_code < 300


def normalize_discovery_url(url: str) -> str:
    """A discovery URL without "/.well-known" gets "/.well-known/oauth-authorization-server" appended."""
    if "/.well-known" in url.lower():
        return url
    return url.rstrip("/") + _WELL_KNOWN_OAUTH


def escape(value: str) -> str:
    """Like Uri.EscapeDataString: everything but unreserved characters is percent-encoded."""
    return quote(value, safe="-_.~")


def build_authorize_url(authorization_endpoint, client_id, redirect_uri, scope, state, code_challenge, quirks: OAuthQuirks) -> str:
    """Authorization URL with PKCE. Parameter order follows the TestTool's Psd2Session.GetOAuthUrlAsync."""
    query = [
        ("redirect_uri", redirect_uri),
        ("response_type", "code"),
        ("client_id", client_id),
        ("code_challenge", code_challenge),
        ("code_challenge_method", PKCE_METHOD),
        ("state", state),
        ("scope", scope + (quirks.scope_suffix or "")),
    ]
    if quirks.nonce is not None:
        query.append(("nonce", quirks.nonce))
    if quirks.ui_locales is not None:
        query.append(("ui_locales", quirks.ui_locales))
    query.extend(quirks.extra_params.items())

    parts = urlsplit(authorization_endpoint)
    pieces = ([parts.query] if parts.query else []) + [f"{escape(k)}={escape(v)}" for k, v in query]
    path = parts.path or "/"
    return f"{parts.scheme}://{parts.netloc}{path}?{'&'.join(pieces)}"


class OAuthClient:
    """Discovery metadata, code exchange and refresh at the token endpoint, over the run's mTLS client."""

    def __init__(self, http: HttpClient, now: Callable[[], datetime]):
        self._http = http
        self._now = now

    def get_metadata(self, discovery_url: str) -> OAuthCallResult:
        return self._send("GET", normalize_discovery_url(discovery_url), [], None, None)

    def exchange_code(self, token_endpoint, client_id, code, redirect_uri, code_verifier) -> OAuthCallResult:
        return self._post_form(token_endpoint, [
            ("client_id", client_id),
            ("grant_type", "authorization_code"),
            ("code", code),
            ("redirect_uri", redirect_uri),
            ("code_verifier", code_verifier),
        ])

    def refresh(self, token_endpoint, client_id, refresh_token) -> OAuthCallResult:
        return self._post_form(token_endpoint, [
            ("client_id", client_id),
            ("grant_type", "refresh_token"),
            ("refresh_token", refresh_token),
        ])

    def _post_form(self, url, form):
        body = "&".join(f"{escape(k)}={escape(v)}" for k, v in form)
        # Never keep the authorization code or refresh token in reports.
        recorded = "&".join(f"{k}=***" if k in ("code", "refresh_token", "code_verifier") else f"{k}={v}" for k, v in form)
        headers = [("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")]
        return self._send("POST", url, headers, body.encode("utf-8"), recorded)

    def _send(self, method, url, headers, body, recorded_body) -> OAuthCallResult:
        exchange = HttpExchange(method=method, url=url, request_headers=list(headers), request_body=recorded_body,
                                started_at_utc=self._now())
        started = time.perf_counter()
        try:
            response = self._http.send(method, prepare_url(url), headers, body)
        except TransportError as ex:
            exchange.duration_ms = int((time.perf_counter() - started) * 1000)
            exchange.error = str(ex)
            return OAuthCallResult(exchange, None)

        exchange.duration_ms = int((time.perf_counter() - started) * 1000)
        exchange.status_code = response.status
        exchange.response_headers = response.headers
        text = response.body.decode("utf-8", errors="replace")
        parsed = jsonpath.try_parse_json(text, response.media_type)
        exchange.response_body = _mask_tokens(parsed) or text
        return OAuthCallResult(exchange, parsed)


def _mask_tokens(parsed) -> str | None:
    """Token responses are recorded with the token values shortened."""
    if not isinstance(parsed, dict) or "access_token" not in parsed:
        return None
    copy = dict(parsed)
    for key in ("access_token", "refresh_token", "id_token"):
        value = copy.get(key)
        if value is not None and str(value):
            value = str(value)
            copy[key] = value[:12] + "…(masked)" if len(value) > 12 else "***"
    return jsonpath.to_json(copy)


# ---- Callback listener -----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class OAuthCallback:
    """What the bank's auth server sent back to the redirect URI."""
    code: str | None
    state: str | None
    error: str | None
    error_description: str | None


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip("[]")
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class OAuthCallbackListener:
    """
    Minimal HTTP listener for the OAuth redirect (e.g. http://localhost:5199/callback) on the loopback interfaces.
    Requests to other paths (favicon, ...) get 404 and the listener keeps waiting.
    """

    def __init__(self, redirect_uri: str):
        parts = urlsplit(redirect_uri) if redirect_uri else None
        if parts is None or parts.scheme != "http" or not is_loopback_host(parts.hostname):
            raise ConfigurationError(f"Manual SCA needs a local http redirect URI (e.g. http://localhost:5199/callback); got '{redirect_uri}'")

        self.redirect_uri = redirect_uri
        self._path = parts.path or "/"
        port = parts.port or 80
        host = parts.hostname

        if host == "127.0.0.1":
            addresses = [(socket.AF_INET, "127.0.0.1")]
        elif host == "::1":
            addresses = [(socket.AF_INET6, "::1")]
        else:
            addresses = [(socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")]

        self._sockets: list[socket.socket] = []
        for family, address in addresses:
            s = socket.socket(family, socket.SOCK_STREAM)
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                else:
                    # macOS/Linux: the previous SCA step's connection may still be in TIME_WAIT on this port; without
                    # SO_REUSEADDR the next scenario's listener fails with "Address already in use". It still does not
                    # allow two active listeners (unlike SO_REUSEPORT).
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((address, port))
                s.listen(8)
                self._sockets.append(s)
            except OSError as ex:
                s.close()
                if family == socket.AF_INET6 and self._sockets and not _in_use(ex):
                    continue  # No IPv6 on this machine; IPv4 is enough.
                self.close()
                raise ConfigurationError(f"Cannot listen on http://{parts.netloc} for the OAuth callback: {ex}") from ex

    def wait(self, timeout_seconds: float, cancel: threading.Event | None = None) -> OAuthCallback:
        """Waits for a request on the redirect path and returns its query parameters. Raises TimeoutError."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancel is not None and cancel.is_set():
                raise OperationCancelled()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"No OAuth callback on {self.redirect_uri} within {timeout_seconds:.0f} s")

            readable, _, _ = select.select(self._sockets, [], [], min(0.5, remaining))
            for s in readable:
                try:
                    client, _ = s.accept()
                except OSError:
                    continue
                with client:
                    callback = self._handle(client)
                    if callback is not None:
                        return callback

    def _handle(self, client: socket.socket) -> OAuthCallback | None:
        client.settimeout(10)
        head = b""
        try:
            while b"\r\n\r\n" not in head and len(head) < 8192:
                chunk = client.recv(4096)
                if not chunk:
                    break
                head += chunk
        except OSError:
            return None

        request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        parts = request_line.split(" ")
        if len(parts) < 2:
            return None

        target = urlsplit(urljoin(self.redirect_uri, parts[1]))
        if target.path != self._path:
            _respond(client, 404, "Not found")
            return None

        q = parse_qs(target.query, keep_blank_values=True)

        def first(name):
            values = q.get(name)
            return values[0] if values else None

        callback = OAuthCallback(first("code"), first("state"), first("error"), first("error_description"))
        message = ("Authorization received. You can close this window and return to ApiTestTool."
                   if callback.error is None
                   else f"The bank returned an error: {html.escape(callback.error)} {html.escape(callback.error_description or '')}")
        _respond(client, 200, message)
        return callback

    def close(self):
        for s in self._sockets:
            s.close()
        self._sockets = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _in_use(ex: OSError) -> bool:
    return getattr(ex, "winerror", None) == 10048 or ex.errno in (48, 98, 10048)


def _respond(client: socket.socket, status: int, message: str):
    page = ('<!doctype html><html><head><meta charset="utf-8"><title>ApiTestTool</title></head>'
            f'<body style="font-family:system-ui;padding:2rem"><p>{message}</p></body></html>').encode("utf-8")
    head = (f"HTTP/1.1 {status} {'OK' if status == 200 else 'Not Found'}\r\nContent-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(page)}\r\nConnection: close\r\n\r\n").encode("ascii")
    try:
        client.sendall(head + page)
    except OSError:
        pass


# ---- Browsers --------------------------------------------------------------------------------------------------

def open_system_browser(url: str, cancel: threading.Event | None = None):
    """Opens the URL in the default browser. If that fails, the tester can still copy the URL from the console."""
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - not fatal, the URL is also shown
        pass


HEADLESS_USER_AGENT = "ApiTestTool-HeadlessSca/1.0"
_MAX_HOPS = 10

_FORM = re.compile(r"<form(?P<attrs>[^>]*)>(?P<body>.*?)</form>", re.I | re.S)
_SUBMIT = re.compile(r"<button[^>]*>(?P<text>.*?)</button>|<input[^>]*type\s*=\s*[\"']?submit[^>]*>", re.I | re.S)
_HIDDEN = re.compile(r"<input[^>]*type\s*=\s*[\"']?hidden[^>]*>", re.I)
_PASSWORD = re.compile(r"<input[^>]*type\s*=\s*[\"']?password", re.I)
_APPROVE_WORD = re.compile(r"approve|allow|accept|confirm|authori[sz]e|grant", re.I)
_REJECT_WORD = re.compile(r"deny|reject|decline|cancel", re.I)


def headless_authorizer(on_error: Callable[[str], None]):
    """
    Stands in for the browser during SCA on test servers: requests the authorize URL, follows redirects, and on a
    simple consent page (a form with an "Approve"-like button and no password field) submits the approval, until
    the auth server redirects to our callback. Never use it against a real bank: it only clicks "approve".
    Returns an open_browser replacement.
    """
    def open_browser(url: str, cancel: threading.Event | None = None):
        # Fire and forget: the SCA step starts waiting for the callback only after open_browser returns.
        threading.Thread(target=_follow, args=(url, on_error), daemon=True, name="aqt-headless-sca").start()

    return open_browser


def _follow(url: str, on_error: Callable[[str], None]):
    browser = _MiniBrowser()
    callback = _redirect_uri_of(url)
    method, target, body = "GET", url, None
    try:
        for _ in range(_MAX_HOPS):
            status, headers, content = browser.send_with_retry(method, target, body)

            # Our own callback answered: the flow is complete.
            if callback is not None and _same_endpoint(target, callback):
                return

            location = headers.get("location")
            if 300 <= status < 400 and location:
                method, target, body = "GET", urljoin(target, location), None
                continue

            page = content.decode("utf-8", errors="replace")
            approval = try_build_approval(page, target) if status == 200 else None
            if approval is not None:
                method, target, body = approval
                continue

            on_error("Automatic SCA: the auth server showed a page without a simple approve button (a real login?). Log in manually instead."
                     if status == 200 else
                     f"Automatic SCA: the auth server answered {status} at {target.split('?')[0]}.")
            return

        on_error("Automatic SCA: too many redirects or pages before reaching the callback.")
    except (OSError, http.client.HTTPException) as ex:
        on_error(f"Automatic SCA: could not complete the authorization: {ex}")


def try_build_approval(page: str, page_url: str):
    """
    Finds a form with an approve-like submit button and returns the POST it would send as (method, url, body).
    Pages with a password field are a real login and are never submitted.
    """
    if _PASSWORD.search(page):
        return None

    for form in _FORM.finditer(page):
        attributes = form.group("attrs")
        body = form.group("body")

        method = _attr(attributes, "method") or "get"
        if method.lower() != "post":
            continue

        approve = None
        for m in _SUBMIT.finditer(body):
            name, value, text = _attr(m.group(0), "name"), _attr(m.group(0), "value"), m.group("text")
            if _is_approval(value) or _is_approval(text):
                approve = (name, value)
                break
        if approve is None:
            continue

        fields = []
        for hidden in _HIDDEN.finditer(body):
            name = _attr(hidden.group(0), "name")
            if name is not None:
                fields.append((name, html.unescape(_attr(hidden.group(0), "value") or "")))
        if approve[0] is not None:
            fields.append((approve[0], html.unescape(approve[1] or "")))

        action = _attr(attributes, "action")
        action_url = page_url if not action else urljoin(page_url, html.unescape(action))
        return "POST", action_url, urlencode(fields).encode("ascii")

    return None


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf"\b{name}\s*=\s*(?:\"(?P<a>[^\"]*)\"|'(?P<b>[^']*)'|(?P<c>[^\s>]+))", tag, re.I)
    if not m:
        return None
    return next(g for g in (m.group("a"), m.group("b"), m.group("c")) if g is not None)


def _is_approval(text: str | None) -> bool:
    return bool(text and text.strip()) and bool(_APPROVE_WORD.search(text)) and not _REJECT_WORD.search(text)


def _redirect_uri_of(authorize_url: str) -> str | None:
    values = parse_qs(urlsplit(authorize_url).query).get("redirect_uri")
    if values and urlsplit(values[0]).scheme:
        return values[0]
    return None


def _same_endpoint(a: str, b: str) -> bool:
    pa, pb = urlsplit(a), urlsplit(b)
    port_a = pa.port or (443 if pa.scheme == "https" else 80)
    port_b = pb.port or (443 if pb.scheme == "https" else 80)
    hosts_match = (pa.hostname or "").lower() == (pb.hostname or "").lower() or (
        is_loopback_host(pa.hostname) and is_loopback_host(pb.hostname))
    return port_a == port_b and (pa.path or "/").lower() == (pb.path or "/").lower() and hosts_match


class _MiniBrowser:
    """Just enough of a browser: cookies per host, no automatic redirects, self-signed TLS only on this machine."""

    def __init__(self):
        self._cookies: dict[str, dict[str, str]] = {}

    def send_with_retry(self, method, url, body):
        # Sandboxes may inject 5xx failures; retry those a few times before giving up.
        attempt = 1
        while True:
            status, headers, content = self._send(method, url, body)
            if attempt >= 4 or status not in (502, 503, 504):
                return status, headers, content
            time.sleep(0.5 * attempt)
            attempt += 1

    def _send(self, method, url, body):
        parts = urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme == "https":
            ctx = ssl.create_default_context()
            if is_loopback_host(host):
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(host, parts.port or 443, timeout=30, context=ctx)
        else:
            conn = http.client.HTTPConnection(host, parts.port or 80, timeout=30)

        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        headers = {"User-Agent": HEADLESS_USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*"}
        jar = self._cookies.get(host.lower())
        if jar:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        try:
            conn.request(method, prepare_url(target), body=body, headers=headers)
            response = conn.getresponse()
            content = response.read()
            for name, value in response.getheaders():
                if name.lower() == "set-cookie":
                    cookie = http.cookies.SimpleCookie()
                    try:
                        cookie.load(value)
                    except http.cookies.CookieError:
                        continue
                    for morsel in cookie.values():
                        self._cookies.setdefault(host.lower(), {})[morsel.key] = morsel.value
            return response.status, {k.lower(): v for k, v in response.getheaders()}, content
        finally:
            conn.close()


def token_response_text(parsed, name: str) -> str | None:
    if isinstance(parsed, dict) and parsed.get(name) is not None:
        return jsonpath.as_text(parsed[name])
    return None
