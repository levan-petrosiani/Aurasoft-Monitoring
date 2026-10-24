"""Step executors: request (with expectations), tls, sca and localCertificates."""

import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

from cryptography import x509

from . import jsonpath
from .certificates import Psd2CertificateInfo
from .context import RunContext
from .errors import ConfigurationError
from .httpclient import TransportError, create_ssl_context, describe, prepare_url
from .oauth import (PKCE_METHOD, OAuthCallbackListener, OAuthClient, OAuthQuirks, build_authorize_url,
                    create_challenge, create_state, create_verifier, token_response_text)
from .results import HttpExchange, StepResult
from .scenarios import Expectation, RequestSpec, Step
from .signing import DIGEST_HEADER, JWS_SIGNATURE_HEADER, SigningMode, verify_response
from .variables import VariableResolver

MAX_RECORDED_BODY_CHARS = 256 * 1024


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _find(headers: list[tuple[str, str]], name: str) -> str | None:
    name = name.lower()
    return next((v for k, v in headers if k.lower() == name), None)


def _set_header(headers: list[tuple[str, str]], name: str, value: str):
    for i, (k, _) in enumerate(headers):
        if k.lower() == name.lower():
            headers[i] = (name, value)
            return
    headers.append((name, value))


def _remove_header(headers: list[tuple[str, str]], name: str):
    headers[:] = [(k, v) for k, v in headers if k.lower() != name.lower()]


# ---- request ---------------------------------------------------------------------------------------------------

def execute_request(step: Step, ctx: RunContext, variables: VariableResolver, result: StepResult):
    spec = step.request
    url = _build_url(spec, ctx, variables)
    headers = _build_headers(spec, ctx, variables)
    body = _build_body(spec, ctx, variables)
    body_bytes = body.encode("utf-8") if body is not None else b""

    if spec.auth is not None:
        token = variables.try_resolve(spec.auth) if spec.auth_optional else variables.resolve(spec.auth)
        if token is not None:
            _set_header(headers, "Authorization", "Bearer " + token)
        else:
            result.info.append("Sent without Authorization: no token was obtained")

    mode = SigningMode(spec.signing)
    if mode != SigningMode.NONE:
        if ctx.signer is None:
            result.warnings.append("Request sent unsigned: no QSealC configured in the profile (tpp.qsealc)")
        else:
            signature = ctx.signer.sign(headers, body_bytes, ctx.now(), mode)
            _set_header(headers, DIGEST_HEADER, signature.digest)
            _set_header(headers, JWS_SIGNATURE_HEADER, signature.jws_signature)

    method = spec.method.upper()
    exchange = HttpExchange(method=method, url=url, request_headers=[(k, _mask(k, v)) for k, v in headers],
                            request_body=body, started_at_utc=ctx.now())
    result.exchange = exchange

    if ctx.profile.limits.delay_between_requests_ms > 0:
        time.sleep(ctx.profile.limits.delay_between_requests_ms / 1000)

    started = time.perf_counter()
    try:
        response = ctx.http.send(method, prepare_url(url), headers, body_bytes if body is not None else None)
    except TransportError as ex:
        exchange.duration_ms = _ms(started)
        exchange.error = str(ex)
        result.failures.append(f"No response from {urlsplit(url).hostname}: {exchange.error}")
        return

    exchange.duration_ms = _ms(started)
    exchange.status_code = response.status
    exchange.response_headers = response.headers

    text = response.body.decode("utf-8", errors="replace")
    exchange.response_body = text[:MAX_RECORDED_BODY_CHARS] + "…(truncated)" if len(text) > MAX_RECORDED_BODY_CHARS else text

    parsed = jsonpath.try_parse_json(text, response.media_type)
    request_id = _find(headers, "X-Request-ID")

    evaluate_expectation(step.expect, exchange, parsed, request_id, response.body, ctx, result)
    _save_variables(step, exchange, parsed, variables, result)


def _build_url(spec: RequestSpec, ctx: RunContext, variables: VariableResolver) -> str:
    if spec.target == "discovery":
        if ctx.profile.endpoints.discovery is None:
            raise ConfigurationError("Step targets 'discovery' but endpoints.discovery is not set in the profile")
        return variables.resolve(ctx.profile.endpoints.discovery)

    if spec.target == "absolute":
        url = variables.resolve(spec.path)
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            raise ConfigurationError(f"Path '{spec.path}' is not an absolute URL")
        return url

    # "v2/consents" is relative to the XS2A base URL; "/0.8/v1/consents/..." (an href from the bank's _links)
    # is relative to the host, as in RFC 3986; absolute URLs are used as they are.
    base = ctx.profile.endpoints.xs2a if ctx.profile.endpoints.xs2a.endswith("/") else ctx.profile.endpoints.xs2a + "/"
    return urljoin(base, variables.resolve(spec.path))


def _build_headers(spec: RequestSpec, ctx: RunContext, variables: VariableResolver) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []

    if spec.headers_template is not None:
        for name, value in ctx.templates.get_headers(spec.headers_template).items():
            if value is not None:
                _set_header(headers, name, variables.resolve(jsonpath.as_text(value)))

    for name, value in (spec.headers or {}).items():
        if value is None:
            _remove_header(headers, name)
        else:
            _set_header(headers, name, variables.resolve(jsonpath.as_text(value)))

    for name in spec.remove_headers or []:
        _remove_header(headers, name)

    return headers


def _build_body(spec: RequestSpec, ctx: RunContext, variables: VariableResolver) -> str | None:
    if spec.body_raw is not None:
        return variables.resolve(spec.body_raw)

    if spec.body_template is None and spec.body is None:
        return None

    node = ctx.templates.get_body(spec.body_template) if spec.body_template is not None else None
    if spec.body is not None:
        node = deep_merge(node, spec.body) if isinstance(node, dict) and isinstance(spec.body, dict) else spec.body

    node = variables.resolve_node(node)

    for path in spec.body_remove or []:
        _remove_path(node, path)

    return jsonpath.to_json(node)


def deep_merge(target: dict, patch: dict) -> dict:
    """Merges patch into target; objects merge recursively, everything else replaces (including null)."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)
        else:
            target[key] = value
    return target


def _remove_path(root, dotted_path: str):
    parts = dotted_path.split(".")
    node = root
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(parts[-1], None)


def _save_variables(step: Step, exchange: HttpExchange, parsed, variables: VariableResolver, result: StepResult):
    for name, source in (step.save or {}).items():
        if source.lower().startswith("header:"):
            value = _find(exchange.response_headers, source[7:].strip())
        else:
            matches = jsonpath.select(parsed, source)
            value = jsonpath.as_text(matches[0]) if matches else None

        if value is None:
            result.failures.append(f"Cannot save '{name}': {source} not found in the response")
        else:
            variables.save(name, value)


def _mask(name: str, value: str) -> str:
    return value[:15] + "…(masked)" if name.lower() == "authorization" and len(value) > 15 else value


# ---- expectations ----------------------------------------------------------------------------------------------

def evaluate_expectation(expect: Expectation | None, exchange: HttpExchange, parsed, request_id: str | None,
                         response_bytes: bytes, ctx: RunContext, result: StepResult):
    status = exchange.status_code
    expected_statuses = _read_statuses(expect.status if expect else None)

    if expected_statuses:
        if status not in expected_statuses:
            result.failures.append(f"Status {status}, expected {' or '.join(str(s) for s in expected_statuses)}")
    elif status >= 500:
        result.failures.append(f"Server error: status {status}")

    if expect is not None:
        _check_headers(expect, exchange, request_id, result)
        _check_content_type(expect, exchange, result)
        _check_json(expect, parsed, exchange, result)
        _check_error_code(expect, parsed, result)

    slow_ms = expect.max_duration_ms if expect and expect.max_duration_ms is not None else ctx.profile.limits.slow_response_ms
    if exchange.duration_ms > slow_ms:
        result.warnings.append(f"Slow response: {exchange.duration_ms} ms (limit {slow_ms} ms)")

    _check_response_signature(expect, exchange, response_bytes, ctx, result)


def _read_statuses(node) -> list[int]:
    if node is None:
        return []
    values = node if isinstance(node, list) else [node]
    if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        raise ConfigurationError("expect.status must be a number or a list of numbers")
    return list(values)


def _check_headers(expect: Expectation, exchange: HttpExchange, request_id: str | None, result: StepResult):
    for name in expect.headers or []:
        if _find(exchange.response_headers, name) is None:
            result.failures.append(f"Response header '{name}' is missing")

    if expect.echo_request_id:
        echoed = _find(exchange.response_headers, "X-Request-ID")
        if echoed is None:
            result.failures.append("Response X-Request-ID is missing")
        elif request_id is not None and echoed.lower() != request_id.lower():
            result.failures.append(f"Response X-Request-ID '{echoed}' does not match request '{request_id}'")


def _check_content_type(expect: Expectation, exchange: HttpExchange, result: StepResult):
    if expect.content_type is None:
        return
    actual = _find(exchange.response_headers, "Content-Type")
    if actual is None or not actual.lower().startswith(expect.content_type.lower()):
        result.failures.append(f"Content-Type '{actual or '(none)'}', expected '{expect.content_type}'")


def _check_json(expect: Expectation, parsed, exchange: HttpExchange, result: StepResult):
    needs_json = bool(expect.json) or bool(expect.exists) or bool(expect.warn_if_missing)
    if needs_json and parsed is None:
        result.failures.append("Response body is empty, JSON expected" if not exchange.response_body
                               else "Response body is not valid JSON")
        return

    for path, expected in (expect.json or {}).items():
        matches = jsonpath.select(parsed, path)
        expected_text = jsonpath.as_text(expected)
        if not matches:
            result.failures.append(f"{path} is missing, expected '{expected_text}'")
        elif not any(jsonpath.as_text(m) == expected_text for m in matches):
            actual = "', '".join(jsonpath.as_text(m) for m in matches)
            result.failures.append(f"{path} is '{actual}', expected '{expected_text}'")

    for path in expect.exists or []:
        if not jsonpath.exists(parsed, path):
            result.failures.append(f"{path} is missing")

    for path in expect.not_exists or []:
        if parsed is not None and jsonpath.exists(parsed, path):
            result.failures.append(f"{path} must not be present")

    for path in expect.warn_if_missing or []:
        if not jsonpath.exists(parsed, path):
            result.warnings.append(f"{path} is missing")


def _check_error_code(expect: Expectation, parsed, result: StepResult):
    if expect.error_code is None:
        return

    codes = []
    for path in ("$.code", "$.additionalErrors[*].code", "$.tppMessages[*].code"):
        for match in jsonpath.select(parsed, path):
            text = jsonpath.as_text(match)
            if text != "null" and text not in codes:
                codes.append(text)

    if not codes:
        result.failures.append("No error code in the response body" if expect.error_code == "*"
                               else f"No error code in the response body, expected '{expect.error_code}'")
    elif expect.error_code != "*" and expect.error_code.lower() not in (c.lower() for c in codes):
        result.failures.append("Error code '" + "', '".join(codes) + f"', expected '{expect.error_code}'")


def _check_response_signature(expect: Expectation | None, exchange: HttpExchange, response_bytes: bytes,
                              ctx: RunContext, result: StepResult):
    mode = expect.response_signature if expect and expect.response_signature else ctx.profile.response_signature
    if mode == "ignore":
        return

    headers = {k: v for k, v in exchange.response_headers}
    verification = verify_response(headers, response_bytes, exchange.status_code, ctx.now())

    if not verification.is_present:
        if mode == "required":
            result.failures.append("Response is not signed (X-Jws-Signature missing)")
        else:
            result.info.append("Response is not signed")
        return

    # A signature that is present must be correct, whatever the mode.
    result.failures.extend("Response signature: " + e for e in verification.errors)
    result.warnings.extend("Response signature: " + w for w in verification.warnings)
    if verification.is_valid:
        result.info.append(f"Response signature valid (signer {verification.signer_organization_id or verification.signer_subject})")


# ---- tls -------------------------------------------------------------------------------------------------------

_PROTOCOLS = {"TLSv1": "Tls", "TLSv1.1": "Tls11", "TLSv1.2": "Tls12", "TLSv1.3": "Tls13"}
_PROTOCOL_ORDER = ["Tls", "Tls11", "Tls12", "Tls13"]


def execute_tls(step: Step, ctx: RunContext, result: StepResult):
    """Opens a TLS connection to the bank (optionally with our QWAC) and reports protocol and server certificate facts."""
    spec = step.tls
    if spec.target == "discovery":
        if ctx.profile.endpoints.discovery is None:
            raise ConfigurationError("tls.target is 'discovery' but endpoints.discovery is not set")
        url = ctx.profile.endpoints.discovery
    else:
        url = ctx.profile.endpoints.xs2a

    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        result.failures.append(f"{url} does not use HTTPS")
        return

    if spec.min_protocol not in ("Tls12", "Tls13"):
        raise ConfigurationError("tls.minProtocol must be Tls12 or Tls13")

    if spec.send_client_certificate and ctx.qwac is None:
        result.warnings.append("No QWAC configured; connecting without a client certificate")

    client_cert = ctx.qwac if spec.send_client_certificate else None
    host, port = parts.hostname, parts.port or 443
    timeout = ctx.profile.limits.timeout_seconds

    def handshake(validate: bool):
        raw = socket.create_connection((host, port), timeout=timeout)
        try:
            return create_ssl_context(client_cert, validate).wrap_socket(raw, server_hostname=host)
        except BaseException:
            raw.close()
            raise

    policy_error = None
    try:
        try:
            tls = handshake(validate=True)
        except ssl.SSLCertVerificationError as ex:
            # Inspect the certificate below instead of aborting.
            policy_error = ex.verify_message or str(ex)
            tls = handshake(validate=False)
    except (ConnectionError, socket.gaierror, socket.timeout, TimeoutError) as ex:
        result.failures.append(f"Cannot connect to {host}:{port}: {describe(ex)}")
        return
    except (ssl.SSLError, OSError) as ex:
        result.failures.append(f"TLS handshake with {host} failed: {describe(ex)}")
        return

    with tls:
        protocol = _PROTOCOLS.get(tls.version(), tls.version() or "unknown")
        cipher = (tls.cipher() or ("unknown",))[0]
        der = tls.getpeercert(binary_form=True)

    result.info.append(f"Protocol {protocol}, cipher {cipher}")
    if protocol in _PROTOCOL_ORDER and _PROTOCOL_ORDER.index(protocol) < _PROTOCOL_ORDER.index(spec.min_protocol):
        result.failures.append(f"Negotiated {protocol}, minimum is {spec.min_protocol}")

    if not der:
        result.failures.append("Server did not present a certificate")
        return

    info = Psd2CertificateInfo.from_cert(x509.load_der_x509_certificate(der))
    now = ctx.now()
    result.info.append(f"Server certificate: {info.subject}")
    result.info.append(f"Issuer: {info.issuer}")
    result.info.append(f"Valid until {info.not_after_utc:%Y-%m-%d} ({info.days_to_expiry(now)} days)")
    if info.organization_id is not None:
        result.info.append(f"Server organizationIdentifier: {info.organization_id}")

    if policy_error is not None:
        message = f"Server certificate is not trusted by this machine: {policy_error}"
        if ctx.profile.tpp.validate_server_certificate:
            result.failures.append(message)
        else:
            result.warnings.append(message + " (accepted because tpp.validateServerCertificate is false)")

    if not info.is_valid_at(now):
        result.failures.append(f"Server certificate is not valid now (valid {info.not_before_utc:%Y-%m-%d} – {info.not_after_utc:%Y-%m-%d})")
    elif info.days_to_expiry(now) < spec.warn_days_before_expiry:
        result.warnings.append(f"Server certificate expires in {info.days_to_expiry(now)} days")


# ---- localCertificates -----------------------------------------------------------------------------------------

def execute_local_certificates(step: Step, ctx: RunContext, result: StepResult):
    """Checks our own TPP certificates before anything is sent to the bank."""
    spec = step.local_certificates
    now = ctx.now()

    def check(name: str, info: Psd2CertificateInfo):
        result.info.append(f"{name}: {info.subject} ({info.key_algorithm}, valid until {info.not_after_utc:%Y-%m-%d})")

        if not info.has_private_key:
            result.failures.append(f"{name} has no private key")

        if not info.is_valid_at(now):
            result.failures.append(f"{name} is not valid now (valid {info.not_before_utc:%Y-%m-%d} – {info.not_after_utc:%Y-%m-%d})")
        elif info.days_to_expiry(now) < spec.warn_days_before_expiry:
            result.warnings.append(f"{name} expires in {info.days_to_expiry(now)} days")

        if info.organization_id is None:
            result.warnings.append(f"{name} has no organizationIdentifier (OID 2.5.4.97); banks use it as the TPP id")
        else:
            result.info.append(f"{name} TPP id: {info.organization_id}")

        if info.key_algorithm not in ("RSA", "ECDSA"):
            result.failures.append(f"{name} key algorithm {info.key_algorithm} is not supported")

    if ctx.qwac_info is None:
        result.failures.append("No QWAC configured (tpp.qwac) — mutual TLS is not possible")
    else:
        check("QWAC", ctx.qwac_info)

    if ctx.qsealc_info is None:
        if spec.require_qsealc:
            result.failures.append("No QSealC configured (tpp.qsealc) — requests cannot be signed")
    else:
        check("QSealC", ctx.qsealc_info)

    qwac_id = ctx.qwac_info.organization_id if ctx.qwac_info else None
    seal_id = ctx.qsealc_info.organization_id if ctx.qsealc_info else None
    if qwac_id and seal_id and qwac_id != seal_id:
        result.failures.append(f"QWAC and QSealC belong to different organisations: {qwac_id} vs {seal_id}")


# ---- sca -------------------------------------------------------------------------------------------------------

def execute_sca(step: Step, ctx: RunContext, variables: VariableResolver, result: StepResult):
    """
    Manual SCA through the bank's OAuth2 server (authorization code + PKCE). Failures of the bank's auth server are
    Failed; problems on our side (no callback in time, port in use, missing configuration) are Error.
    """
    spec = step.sca
    oauth = OAuthClient(ctx.http, ctx.now)

    if spec.discovery is not None:
        discovery = variables.resolve(spec.discovery)
    elif ctx.profile.endpoints.discovery is not None:
        discovery = ctx.profile.endpoints.discovery
    else:
        raise ConfigurationError("sca.discovery is not set and the profile has no endpoints.discovery")

    scope = variables.resolve(spec.scope)
    if spec.client_id is not None:
        client_id = variables.resolve(spec.client_id)
    elif ctx.tpp_id is not None:
        client_id = ctx.tpp_id
    else:
        raise ConfigurationError("sca needs a client_id: configure a QWAC with an organizationIdentifier or set sca.clientId")
    redirect_uri = variables.resolve(spec.redirect_uri or ctx.profile.tpp.redirect_uri)
    quirks = OAuthQuirks.from_profile(ctx.profile.quirks)

    # 1. Discovery
    metadata = oauth.get_metadata(discovery)
    _record(result, metadata.exchange)
    if not _check_call(metadata, "Discovery document", result):
        return

    authorization_endpoint = token_response_text(metadata.json, "authorization_endpoint")
    token_endpoint = token_response_text(metadata.json, "token_endpoint")
    if authorization_endpoint is None or token_endpoint is None:
        result.failures.append("Discovery document has no authorization_endpoint and/or token_endpoint")
        return

    methods = metadata.json.get("code_challenge_methods_supported")
    if isinstance(methods, list) and PKCE_METHOD not in [str(m) for m in methods]:
        result.warnings.append("Discovery document does not list S256 in code_challenge_methods_supported")

    # 2. Authorize in the browser, wait for the callback
    verifier = create_verifier()
    state = create_state(scope.split(":")[0])
    authorize_url = build_authorize_url(authorization_endpoint, client_id, redirect_uri, scope, state,
                                        create_challenge(verifier), quirks)
    result.info.append(f"Authorize URL: {authorize_url}")

    with OAuthCallbackListener(redirect_uri) as listener:
        ctx.notify(f"Log in with a sandbox PSU to authorise '{scope}'. Waiting up to {spec.timeout_seconds} s for {redirect_uri} ...")
        ctx.notify(f"If no browser opened, open: {authorize_url}")
        ctx.open_browser(authorize_url, ctx.cancel)

        try:
            callback = listener.wait(spec.timeout_seconds, ctx.cancel)
        except TimeoutError as ex:
            result.failures.append(f"{ex} — SCA was not completed in the browser")
            result.is_tool_error = True
            return

    if callback.error is not None:
        description = f": {callback.error_description}" if callback.error_description is not None else ""
        result.failures.append(f"Auth server redirected with error '{callback.error}'{description}")
        return

    if callback.state != state:
        result.failures.append(f"Callback state '{callback.state}' does not match the state we sent")
        return

    if not callback.code:
        result.failures.append("Callback has no authorization code")
        return

    # 3. Code -> token
    token = oauth.exchange_code(token_endpoint, client_id, callback.code, redirect_uri, verifier)
    _record(result, token.exchange)
    if not _check_call(token, "Token request", result) or not _check_token_response(token.json, spec, variables, result, scope, "Access token received"):
        return

    # 4. Optional refresh
    if not spec.refresh:
        return

    refresh_token = variables.saved.get(spec.save_refresh_token_as)
    if refresh_token is None:
        result.warnings.append("Refresh not tested: the token response has no refresh_token")
        return

    refreshed = oauth.refresh(token_endpoint, client_id, refresh_token)
    _record(result, refreshed.exchange)
    if _check_call(refreshed, "Refresh token request", result):
        _check_token_response(refreshed.json, spec, variables, result, scope, "Access token refreshed")


def _check_call(call, what: str, result: StepResult) -> bool:
    if call.exchange.error is not None:
        result.failures.append(f"{what}: no response from {urlsplit(call.exchange.url).hostname}: {call.exchange.error}")
        return False

    if not call.is_success:
        error = token_response_text(call.json, "error")
        description = token_response_text(call.json, "error_description")
        result.failures.append(f"{what}: status {call.exchange.status_code}"
                               + (f", error '{error}'" if error is not None else "")
                               + (f": {description}" if description is not None else ""))
        return False

    if not isinstance(call.json, dict):
        result.failures.append(f"{what}: response is not a JSON object")
        return False

    return True


def _check_token_response(parsed, spec, variables: VariableResolver, result: StepResult, scope: str, label: str) -> bool:
    access_token = token_response_text(parsed, "access_token")
    if not access_token:
        result.failures.append("Token response has no access_token")
        return False

    variables.save(spec.save_access_token_as, access_token)

    refresh = token_response_text(parsed, "refresh_token")
    if refresh:
        variables.save(spec.save_refresh_token_as, refresh)

    token_type = token_response_text(parsed, "token_type")
    if (token_type or "").lower() != "bearer":
        result.warnings.append(f"token_type is '{token_type or '(missing)'}', expected 'Bearer'")

    if parsed.get("expires_in") is None:
        result.warnings.append("Token response has no expires_in")

    expect_in_scope = scope.split(":")[-1] if spec.expect_scope_contains is None else variables.resolve(spec.expect_scope_contains)
    granted = token_response_text(parsed, "scope")
    if granted is not None and expect_in_scope not in granted:
        result.warnings.append(f"Granted scope '{granted}' does not contain '{expect_in_scope}'")

    expires = parsed.get("expires_in")
    result.info.append(f"{label} ({f'expires in {jsonpath.as_text(expires)} s' if expires is not None else 'no expiry'})")
    return True


def _record(result: StepResult, exchange: HttpExchange):
    if result.exchange is None:
        result.exchange = exchange
    else:
        result.exchanges.append(exchange)
