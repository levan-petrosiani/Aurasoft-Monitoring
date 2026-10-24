"""
Bank profiles: everything the tool needs to know about one bank's sandbox (endpoints, our TPP certificates,
supported features, test data). One YAML file per bank in banks/.
"""

import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from . import yamljson
from .binding import bind, required, skip
from .errors import ConfigurationError


@dataclass
class BankInfo:
    bic: str = required()
    name: str = required()
    # Specification the bank claims to implement (informational for now).
    spec: str | None = None


@dataclass
class EndpointSettings:
    # XS2A sandbox base URL, e.g. https://psd2-xs2a-sandbox.bank.ge/
    xs2a: str = required()
    # OAuth2/OIDC discovery document of the bank's auth server.
    discovery: str | None = None


@dataclass
class SelfSignedSettings:
    # PSD2 authorisation number put in the organizationIdentifier (OID 2.5.4.97), i.e. our TPP id.
    organization_id: str = "PSDGE-NBG-MOCKTPP"
    common_name: str = "ApiTestTool Mock TPP"


@dataclass
class CertificateSettings:
    # PFX file, relative to the profile file.
    file: str | None = None
    # PFX password. Prefer password_env so passwords stay out of files.
    password: str | None = None
    # Environment variable that holds the PFX password.
    password_env: str | None = None
    # Windows certificate store, e.g. CurrentUser/My (not supported by the Python edition).
    store: str | None = None
    thumbprint: str | None = None
    # Generate a throw-away self-signed certificate for this run (MockBank and similar test servers only).
    self_signed: SelfSignedSettings | None = None


@dataclass
class TppSettings:
    # QWAC used for mutual TLS.
    qwac: CertificateSettings | None = None
    # QSealC used for request signatures (X-Jws-Signature).
    qsealc: CertificateSettings | None = None
    # Redirect URI registered at the bank, sent as TPP-Redirect-URI.
    redirect_uri: str = "http://localhost:5199/callback"
    # Reject server certificates that do not chain to a trusted root. Disable only for sandboxes with test CAs.
    validate_server_certificate: bool = True


@dataclass
class RetrySettings:
    # Total attempts including the first one (2 = one retry).
    attempts: int = 3
    delay_ms: int = 500
    # Statuses treated as transient. Connection errors and timeouts are always transient.
    statuses: list[int] = field(default_factory=lambda: [502, 503, 504])


@dataclass
class LimitSettings:
    timeout_seconds: int = 30
    # Pause between requests, to stay within the bank's rate limits.
    delay_between_requests_ms: int = 0
    # Response time above which a warning is reported.
    slow_response_ms: int = 3000
    # Retry of transient failures. Off by default, because for quality monitoring an outage is a finding.
    retry: RetrySettings | None = None


@dataclass
class BankProfile:
    bank: BankInfo = required()
    endpoints: EndpointSettings = required()
    tpp: TppSettings = field(default_factory=TppSettings)
    # What the bank supports. Scenarios declare `requires` paths into the profile and are skipped otherwise.
    features: Any = None
    # Free-form test data (PSUs, IBANs, ...), available in scenarios as {{vars.*}}.
    variables: Any = None
    # Bank-specific deviations, e.g. extra OAuth parameters.
    quirks: Any = None
    limits: LimitSettings = field(default_factory=LimitSettings)
    # Default for checking the bank's response signatures: required, optional (verify when present) or ignore.
    response_signature: str = "optional"

    # Path of the YAML file this profile was loaded from.
    source_path: str = skip("")
    # The raw profile document, used to evaluate scenario `requires` paths.
    raw: Any = skip()


def load(bic_or_path: str, banks_directory: str) -> BankProfile:
    """Loads a profile by file path, or by BIC from banks_directory (<BIC>.yaml)."""
    path = bic_or_path if os.path.isfile(bic_or_path) else os.path.join(banks_directory, f"{bic_or_path}.yaml")
    if not os.path.isfile(path):
        raise ConfigurationError(f"Bank profile '{bic_or_path}' not found (looked for {os.path.abspath(path)})")
    return load_file(path)


def load_file(path: str) -> BankProfile:
    raw = _load_with_base(path, 0)
    try:
        profile = bind(BankProfile, raw)
    except ConfigurationError as ex:
        raise ConfigurationError(f"{path}: {ex}") from ex

    _validate(profile, path)
    profile.source_path = os.path.abspath(path)
    profile.raw = raw
    return profile


def list_profiles(banks_directory: str) -> list[str]:
    """All profiles in a directory. Files starting with '_' are templates and are skipped."""
    if not os.path.isdir(banks_directory):
        return []
    files = [os.path.join(banks_directory, f) for f in os.listdir(banks_directory)
             if f.lower().endswith(".yaml") and not f.startswith("_")]
    return sorted(files, key=str.lower)


def _load_with_base(path: str, depth: int) -> dict:
    """
    Reads a profile and, when it has `extends: other.yaml`, deep-merges it over that base file
    (relative to the profile). Shared settings such as TPP certificates live in one base file.
    """
    if depth > 5:
        raise ConfigurationError(f"{path}: 'extends' chain is too deep")

    raw = yamljson.parse_file(path)
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path}: profile must be a YAML mapping")

    extends = raw.pop("extends", None)
    if extends is None:
        return raw

    base_path = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(path)), str(extends)))
    if not os.path.isfile(base_path):
        raise ConfigurationError(f"{path}: base profile '{base_path}' not found")

    return _merge(_load_with_base(base_path, depth + 1), raw)


def _merge(target: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value
    return target


def is_absolute_url(value: str | None, schemes=None) -> bool:
    if not value:
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    if not parts.scheme or not parts.netloc:
        return False
    return schemes is None or parts.scheme.lower() in schemes


def _validate(profile: BankProfile, path: str):
    if not is_absolute_url(profile.endpoints.xs2a, ("http", "https")):
        raise ConfigurationError(f"{path}: endpoints.xs2a must be an absolute http(s) URL")

    if profile.endpoints.discovery is not None and not is_absolute_url(profile.endpoints.discovery):
        raise ConfigurationError(f"{path}: endpoints.discovery must be an absolute URL")

    if profile.response_signature not in ("required", "optional", "ignore"):
        raise ConfigurationError(f"{path}: responseSignature must be required, optional or ignore")
