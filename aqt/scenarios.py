"""Scenarios (one test case per YAML file in scenarios/) and reusable templates (scenarios/_templates)."""

import copy
import os
from dataclasses import dataclass
from typing import Any

from . import yamljson
from .binding import bind, required, skip
from .errors import ConfigurationError

TEMPLATES_FOLDER = "_templates"


@dataclass
class RequestSpec:
    method: str = "GET"
    # Path relative to the target base URL; may contain a query string and {{variables}}.
    path: str = required()
    # xs2a (default), discovery (path ignored, the discovery URL is used) or absolute (path is a full URL).
    target: str = "xs2a"
    # Named header set from scenarios/_templates.
    headers_template: str | None = None
    # Headers added on top of the template. A null value removes the header.
    headers: dict[str, Any] | None = None
    remove_headers: list[str] | None = None
    # Named JSON body from scenarios/_templates.
    body_template: str | None = None
    # JSON body; when a template is used, its properties are deep-merged over the template.
    body: Any = None
    # Dotted paths removed from the body after merging, e.g. access or access.payments.
    body_remove: list[str] | None = None
    # Body sent exactly as written (for malformed JSON tests). Overrides template and body.
    body_raw: str | None = None
    # normal, none, corruptSignature or wrongDigest.
    signing: str = "normal"
    # Bearer token for the Authorization header.
    auth: str | None = None
    # Send the request without Authorization when `auth` refers to a variable that was never saved.
    auth_optional: bool = False


@dataclass
class TlsSpec:
    target: str = "xs2a"
    # Lowest acceptable protocol: Tls12 or Tls13.
    min_protocol: str = "Tls12"
    warn_days_before_expiry: int = 30
    send_client_certificate: bool = True


@dataclass
class ScaSpec:
    """
    Strong customer authentication through the bank's OAuth2 server (authorization code + PKCE):
    discovery -> authorize in a browser -> callback on the redirect URI -> token exchange (-> optional refresh).
    """
    # Only manual for now: the tester logs in with a sandbox PSU in the system browser.
    mode: str = "manual"
    # Discovery URL or auth server base URL, e.g. {{scaOAuthUrl}}. Defaults to the profile's endpoints.discovery.
    discovery: str | None = None
    # OAuth scope, e.g. AIS:{{consentId}}.
    scope: str = required()
    # OAuth client_id. Defaults to our TPP id from the QWAC.
    client_id: str | None = None
    # Defaults to the profile's tpp.redirectUri.
    redirect_uri: str | None = None
    # How long to wait for the tester to finish logging in.
    timeout_seconds: int = 300
    # Also exercise the refresh_token grant after the code exchange.
    refresh: bool = False
    save_access_token_as: str = "token"
    save_refresh_token_as: str = "refreshToken"
    # Warn when the token's scope does not contain this value, e.g. the consent id.
    expect_scope_contains: str | None = None


@dataclass
class LocalCertificatesSpec:
    warn_days_before_expiry: int = 30
    require_qsealc: bool = True


@dataclass
class Expectation:
    # Expected HTTP status: a number or a list of numbers.
    status: Any = None
    # Response headers that must be present.
    headers: list[str] | None = None
    # The response X-Request-ID must equal the request's.
    echo_request_id: bool = False
    # JSON path -> expected value (compared as text, case-sensitive).
    json: dict[str, Any] | None = None
    exists: list[str] | None = None
    not_exists: list[str] | None = None
    # Paths whose absence is reported as a warning, not a failure.
    warn_if_missing: list[str] | None = None
    # Expected error code ("*" accepts any code but requires one).
    error_code: str | None = None
    # Expected media type prefix, e.g. application/json or application/problem+json.
    content_type: str | None = None
    max_duration_ms: int | None = None
    # Overrides the profile's responseSignature for this step: required, optional or ignore.
    response_signature: str | None = None


@dataclass
class Step:
    """A scenario step. Exactly one of request, tls, localCertificates, sca is set."""
    name: str | None = None
    request: RequestSpec | None = None
    tls: TlsSpec | None = None
    local_certificates: LocalCertificatesSpec | None = None
    sca: ScaSpec | None = None
    # Variables that must have been saved by earlier steps; otherwise this step is skipped (useful in cleanup).
    run_if: list[str] | None = None
    expect: Expectation | None = None
    # Values to keep for later steps: variable name -> JSON path ($.consentId) or header:Location.
    save: dict[str, str] | None = None

    @property
    def kind(self) -> str:
        if self.request is not None:
            return "request"
        if self.tls is not None:
            return "tls"
        if self.sca is not None:
            return "sca"
        return "localCertificates"

    @property
    def kind_count(self) -> int:
        return sum(x is not None for x in (self.request, self.tls, self.local_certificates, self.sca))

    def display_name(self, index: int) -> str:
        if self.name is not None:
            return self.name
        if self.request is not None:
            return f"{self.request.method.upper()} {self.request.path}"
        return f"step {index + 1} ({self.kind})"


@dataclass
class Scenario:
    id: str = required()
    title: str = required()
    # Suite name used for filtering, e.g. preflight, negative, ais, pis.
    suite: str = required()
    # Critical, Major, Minor or Info.
    severity: str = "Major"
    # Where in the standard the expected behaviour is defined.
    spec_ref: str | None = None
    # Where the scenario came from, e.g. the TestTool BadRequest method it was ported from.
    origin: str | None = None
    description: str | None = None
    # Profile paths that must be present and truthy (e.g. endpoints.discovery), otherwise the scenario is skipped.
    requires: list[str] | None = None
    steps: list[Step] = required()
    # Steps that always run at the end (e.g. delete the consent). Their failures become warnings.
    cleanup: list[Step] | None = None

    source_path: str = skip("")


def load_all(directory: str) -> list[Scenario]:
    """
    Loads every scenario under directory (recursively). The _templates folder and files starting with '_'
    are skipped. Raises ConfigurationError listing every invalid file.
    """
    if not os.path.isdir(directory):
        raise ConfigurationError(f"Scenarios directory not found: {os.path.abspath(directory)}")

    files = []
    for root, _, names in os.walk(directory):
        files.extend(os.path.join(root, n) for n in names if n.lower().endswith(".yaml"))

    scenarios: list[Scenario] = []
    errors: list[str] = []

    for file in sorted(files, key=str.lower):
        relative = os.path.relpath(file, directory)
        if any(part.startswith("_") for part in relative.replace("\\", "/").split("/")):
            continue

        try:
            raw = yamljson.parse_file(file)
            if raw is None:
                raise ConfigurationError(f"{file}: file is empty")
            try:
                scenario = bind(Scenario, raw)
            except ConfigurationError as ex:
                raise ConfigurationError(f"{file}: {ex}") from ex
            _validate(scenario, file)
            scenario.source_path = os.path.abspath(file)
            scenarios.append(scenario)
        except ConfigurationError as ex:
            errors.append(str(ex))

    by_id: dict[str, list[Scenario]] = {}
    for s in scenarios:
        by_id.setdefault(s.id.lower(), []).append(s)
    for group in by_id.values():
        if len(group) > 1:
            errors.append(f"Duplicate scenario id '{group[0].id}': {', '.join(s.source_path for s in group)}")

    if errors:
        raise ConfigurationError("Invalid scenarios:" + os.linesep + os.linesep.join("  - " + e for e in errors))

    return scenarios


def filter_scenarios(scenarios, suites, ids) -> list[Scenario]:
    """Filters by suite names and/or scenario ids (case-insensitive). Empty filters match everything."""
    suites = {s.lower() for s in suites}
    ids = {i.lower() for i in ids}
    return [s for s in scenarios
            if (not suites or s.suite.lower() in suites) and (not ids or s.id.lower() in ids)]


def _validate(scenario: Scenario, file: str):
    if not scenario.steps:
        raise ConfigurationError(f"{file}: scenario has no steps")

    for index, step in enumerate(scenario.steps + (scenario.cleanup or [])):
        if step.kind_count != 1:
            raise ConfigurationError(f"{file}: step {index + 1} must have exactly one of 'request', 'tls', 'localCertificates', 'sca'")

        if step.sca is not None:
            if step.sca.mode != "manual":
                raise ConfigurationError(f"{file}: step {index + 1}: sca.mode '{step.sca.mode}' is not supported yet (manual)")
            if not 5 <= step.sca.timeout_seconds <= 3600:
                raise ConfigurationError(f"{file}: step {index + 1}: sca.timeoutSeconds must be between 5 and 3600")

        if step.request is not None:
            if step.request.signing not in ("normal", "none", "corruptSignature", "wrongDigest"):
                raise ConfigurationError(f"{file}: step {index + 1}: signing must be normal, none, corruptSignature or wrongDigest")
            if step.request.target not in ("xs2a", "discovery", "absolute"):
                raise ConfigurationError(f"{file}: step {index + 1}: target must be xs2a, discovery or absolute")

        rs = step.expect.response_signature if step.expect else None
        if rs is not None and rs not in ("required", "optional", "ignore"):
            raise ConfigurationError(f"{file}: step {index + 1}: responseSignature must be required, optional or ignore")

    if scenario.severity not in ("Critical", "Major", "Minor", "Info"):
        raise ConfigurationError(f"{file}: severity must be Critical, Major, Minor or Info")


class TemplateLibrary:
    """
    Reusable header sets and JSON bodies from scenarios/_templates/*.yaml:

        headers:
          consent: { X-Request-ID: "{{uuid}}", ... }
        bodies:
          consent.accountList: { ... }
    """

    def __init__(self):
        self._headers: dict[str, tuple[str, dict]] = {}
        self._bodies: dict[str, tuple[str, Any]] = {}

    @property
    def header_set_names(self) -> list[str]:
        return [name for name, _ in self._headers.values()]

    @property
    def body_names(self) -> list[str]:
        return [name for name, _ in self._bodies.values()]

    @staticmethod
    def load(scenarios_directory: str) -> "TemplateLibrary":
        library = TemplateLibrary()
        folder = os.path.join(scenarios_directory, TEMPLATES_FOLDER)
        if not os.path.isdir(folder):
            return library

        for file in sorted((f for f in os.listdir(folder) if f.lower().endswith(".yaml")), key=str.lower):
            path = os.path.join(folder, file)
            root = yamljson.parse_file(path)
            if not isinstance(root, dict):
                continue

            for section, entries in root.items():
                if not isinstance(entries, dict):
                    raise ConfigurationError(f"{path}: '{section}' must be a mapping")

                for name, entry in entries.items():
                    if section == "headers" and isinstance(entry, dict):
                        library._headers[name.lower()] = (name, entry)
                    elif section == "bodies" and entry is not None:
                        library._bodies[name.lower()] = (name, entry)
                    else:
                        raise ConfigurationError(f"{path}: unknown template section '{section}' or invalid entry '{name}'")

        return library

    def get_headers(self, name: str) -> dict:
        entry = self._headers.get(name.lower())
        if entry is None:
            raise ConfigurationError(f"Header template '{name}' not found")
        return copy.deepcopy(entry[1])

    def get_body(self, name: str):
        entry = self._bodies.get(name.lower())
        if entry is None:
            raise ConfigurationError(f"Body template '{name}' not found")
        return copy.deepcopy(entry[1])
