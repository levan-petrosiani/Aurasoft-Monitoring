"""
Removes secrets and personal data before anything is stored or sent to a model: auth headers, OAuth codes and tokens
(JSON, form and query string), PSU identifiers and, optionally, IBANs. The engine already shortens the Authorization
header and token responses in reports; this is the second, stricter layer for the store and the AI.
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote, unquote_plus

_MARKER = "(masked)"

_HEADERS = {"authorization", "proxy-authorization", "cookie", "set-cookie", "psu-id", "psu-corporate-id"}

_KEYS = {"access_token", "refresh_token", "id_token", "code_verifier", "client_secret", "client_assertion", "password"}

# OAuth authorization `code`: secret in form bodies and query strings (token request, callback URL), but in JSON it is
# the XS2A error code (tppMessages[].code = FORMAT_ERROR), which must stay readable.
_FORM_ONLY_KEYS = {"code"}

# IBAN: 2 letters, 2 check digits, 11-30 alphanumerics (Georgian IBANs are 22 characters).
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b")


@dataclass
class MaskingOptions:
    # Replace the middle of IBANs with '*' (keeps country, check digits and the last 4 characters).
    mask_ibans: bool = True
    # Additional header names whose values are masked.
    extra_headers: list[str] = field(default_factory=list)
    # Additional JSON/form keys whose values are masked (any depth).
    extra_keys: list[str] = field(default_factory=list)


def mask_value(value: str) -> str:
    """Hides the whole value; only the auth scheme is kept. Values the engine already shortened are masked fully too."""
    scheme, sep, _ = value.partition(" ")
    if sep and 0 < len(scheme) < 10 and scheme.lower() in ("bearer", "basic"):
        return f"{scheme} ***{_MARKER}"
    return "***" + _MARKER


def cap(text: str | None, max_chars: int) -> tuple[str | None, bool]:
    """Cuts text to max_chars; returns (text, truncated)."""
    if text is None or len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "…(truncated)", True


class SensitiveDataMasker:
    def __init__(self, options: MaskingOptions | None = None):
        self.options = options or MaskingOptions()
        self._extra_headers = {h.lower() for h in self.options.extra_headers}
        self._extra_keys = {k.lower() for k in self.options.extra_keys}

    def mask_header(self, name: str, value: str) -> str:
        lowered = name.lower()
        return mask_value(value) if lowered in _HEADERS or lowered in self._extra_headers else value

    def mask_headers(self, headers) -> list[tuple[str, str]]:
        return [(k, self.mask_header(k, v)) for k, v in headers]

    def mask_url(self, url: str) -> str:
        """Masks secrets in a query string (e.g. the OAuth callback ?code=...)."""
        base, sep, query = url.partition("?")
        return self._mask_ibans(base) + ("?" + self._mask_form(query) if sep else "")

    def mask_body(self, body: str | None) -> str | None:
        """Masks a request/response body: JSON, form-urlencoded or plain text."""
        if not body:
            return body
        stripped = body.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                node = json.loads(body)
            except ValueError:
                node = None
            else:
                return json.dumps(self._mask_node(node), ensure_ascii=False, separators=(",", ":"))
        if " " not in body and "=" in body and "\n" not in body:
            return self._mask_form(body)
        return self._mask_ibans(body)

    def _mask_node(self, node):
        if isinstance(node, dict):
            result = {}
            for key, value in node.items():
                if isinstance(value, str) and self._is_secret(key):
                    result[key] = mask_value(value)
                elif isinstance(value, str):
                    result[key] = self._mask_ibans(value)
                else:
                    result[key] = self._mask_node(value)
            return result
        if isinstance(node, list):
            return [self._mask_ibans(v) if isinstance(v, str) else self._mask_node(v) for v in node]
        return node

    def _mask_form(self, form: str) -> str:
        parts = form.split("&")
        for i, part in enumerate(parts):
            key, sep, value = part.partition("=")
            if not sep or not key:
                continue
            decoded = unquote_plus(key)
            if self._is_secret(decoded) or decoded.lower() in _FORM_ONLY_KEYS:
                parts[i] = f"{key}={quote(mask_value(unquote_plus(value)), safe='')}"
        return self._mask_ibans("&".join(parts))

    def _is_secret(self, key: str) -> bool:
        lowered = key.lower()
        return lowered in _KEYS or lowered in self._extra_keys

    def _mask_ibans(self, text: str) -> str:
        if not self.options.mask_ibans:
            return text
        return _IBAN.sub(lambda m: m.group()[:4] + "*" * (len(m.group()) - 8) + m.group()[-4:], text)


DEFAULT = SensitiveDataMasker()
