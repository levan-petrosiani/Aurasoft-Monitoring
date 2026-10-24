"""
HTTP/1.1 client for XS2A calls: the QWAC as TLS client certificate (mutual TLS), no redirects, no decompression,
optional retries of transient failures. Built on http.client so the tool controls exactly which headers go out.
"""

import http.client
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from .certificates import Certificate
from .profiles import BankProfile, RetrySettings

# Added to responses that needed retries, e.g. "503, 503". Never sent to the bank.
RETRIED_HEADER = "X-AQT-Retried"

_SAFE_URL_CHARS = ":/?#[]@!$&'()*+,;=%-._~"


class TransportError(Exception):
    """No response: connection refused, TLS failure, timeout."""


@dataclass
class HttpResponse:
    status: int
    headers: list[tuple[str, str]]
    body: bytes

    def header(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers if k.lower() == name), None)

    @property
    def media_type(self) -> str | None:
        ct = self.header("Content-Type")
        return ct.split(";")[0].strip() if ct else None


def create_ssl_context(client_certificate: Certificate | None, validate: bool) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if validate:
        ctx.load_default_certs()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    if client_certificate is not None:
        cert_file, key_file = client_certificate.pem_files()
        ctx.load_cert_chain(cert_file, key_file)
    return ctx


def describe(ex: BaseException) -> str:
    if isinstance(ex, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(ex, ssl.SSLCertVerificationError):
        return f"The remote certificate is invalid: {ex.verify_message}"
    if isinstance(ex, ssl.SSLError):
        return f"TLS error: {ex.reason or ex}"
    if isinstance(ex, ConnectionRefusedError):
        return "Connection refused"
    if isinstance(ex, socket.gaierror):
        return f"Name resolution failed: {ex.strerror or ex}"
    text = str(ex).strip()
    return text or type(ex).__name__


def prepare_url(url: str) -> str:
    """Escapes characters http.client refuses (spaces etc.), keeping existing %-escapes."""
    return quote(url, safe=_SAFE_URL_CHARS)


class HttpClient:
    """One client per run. Connections are kept alive per host and reused, like HttpClient's pool."""

    def __init__(self, ssl_context: ssl.SSLContext | None, timeout_seconds: float, retry: RetrySettings | None = None):
        self._ssl = ssl_context
        self._timeout = timeout_seconds
        self._retry = retry if retry is not None and retry.attempts > 1 else None
        self._pool: dict[tuple[str, str, int], http.client.HTTPConnection] = {}
        self._lock = threading.Lock()

    @staticmethod
    def for_profile(profile: BankProfile, qwac: Certificate | None) -> "HttpClient":
        ctx = create_ssl_context(qwac, profile.tpp.validate_server_certificate)
        return HttpClient(ctx, profile.limits.timeout_seconds, profile.limits.retry)

    def send(self, method: str, url: str, headers: list[tuple[str, str]], body: bytes | None = None) -> HttpResponse:
        if self._retry is None:
            return self._send_once(method, url, headers, body)

        reasons = []
        attempt = 1
        while True:
            last = attempt >= max(1, self._retry.attempts)
            try:
                response = self._send_once(method, url, headers, body)
                if last or response.status not in self._retry.statuses:
                    if reasons:
                        response.headers.append((RETRIED_HEADER, ", ".join(reasons)))
                    return response
                reasons.append(str(response.status))
            except TransportError as ex:
                if last:
                    raise
                reasons.append("timeout" if str(ex) == "timeout" else "ConnectionError")

            time.sleep(self._retry.delay_ms * attempt / 1000)
            attempt += 1

    def _send_once(self, method, url, headers, body) -> HttpResponse:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme not in ("http", "https"):
            raise TransportError(f"Unsupported URL scheme '{parts.scheme}'")
        port = parts.port or (443 if scheme == "https" else 80)
        host = parts.hostname or ""
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        key = (scheme, host, port)

        for attempt in (1, 2):
            conn, reused = self._connection(key)
            try:
                conn.putrequest(method, target, skip_accept_encoding=True)
                has_content_type = any(k.lower() == "content-type" for k, _ in headers)
                if body is not None and not has_content_type:
                    conn.putheader("Content-Type", "application/json")
                for name, value in headers:
                    conn.putheader(name, value)
                if body is not None:
                    conn.putheader("Content-Length", str(len(body)))
                elif method.upper() in ("POST", "PUT", "PATCH"):
                    conn.putheader("Content-Length", "0")
                conn.endheaders(body)

                response = conn.getresponse()
                data = response.read()
                result = HttpResponse(response.status, _merge_headers(response.getheaders()), data)
                if response.will_close:
                    self._drop(key, conn)
                else:
                    self._release(key, conn)
                return result
            except (http.client.RemoteDisconnected, ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
                    http.client.BadStatusLine) as ex:
                self._drop(key, conn)
                # A kept-alive connection may have been closed by the server in the meantime: retry once on a new one.
                if reused and attempt == 1:
                    continue
                raise TransportError(describe(ex)) from ex
            except (OSError, http.client.HTTPException) as ex:
                self._drop(key, conn)
                raise TransportError(describe(ex)) from ex

        raise TransportError("connection failed")

    def _connection(self, key):
        with self._lock:
            conn = self._pool.pop(key, None)
        if conn is not None:
            return conn, True

        scheme, host, port = key
        if scheme == "https":
            conn = http.client.HTTPSConnection(host, port, timeout=self._timeout, context=self._ssl)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self._timeout)
        return conn, False

    def _release(self, key, conn):
        with self._lock:
            old = self._pool.get(key)
            self._pool[key] = conn
        if old is not None and old is not conn:
            old.close()

    def _drop(self, key, conn):
        conn.close()

    def close(self):
        with self._lock:
            pool, self._pool = self._pool, {}
        for conn in pool.values():
            conn.close()


def _merge_headers(items) -> list[tuple[str, str]]:
    """Repeated headers are joined with ", " like HttpClient's header collections."""
    merged: dict[str, tuple[str, list[str]]] = {}
    for name, value in items:
        entry = merged.setdefault(name.lower(), (name, []))
        entry[1].append(value)
    return [(name, ", ".join(values)) for name, values in merged.values()]
