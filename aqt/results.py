"""Run results. Serialized to summary.json in the same shape as the C# tool (camelCase, nulls omitted)."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Outcome(str, Enum):
    # All expectations met.
    PASSED = "Passed"
    # The bank's API did not behave as expected.
    FAILED = "Failed"
    # Not executed because an earlier step failed.
    BLOCKED = "Blocked"
    # Not applicable: the bank does not support a required feature.
    SKIPPED = "Skipped"
    # Problem on our side: configuration, certificate, unknown variable.
    ERROR = "Error"

    def __str__(self):
        return self.value


def format_time(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _pairs(items):
    return [{"key": k, "value": v} for k, v in items]


def _unpairs(items):
    return [(i.get("key", ""), i.get("value", "")) for i in items or []]


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


@dataclass
class HttpExchange:
    method: str
    url: str
    request_headers: list[tuple[str, str]]
    request_body: str | None = None
    status_code: int | None = None
    response_headers: list[tuple[str, str]] = field(default_factory=list)
    response_body: str | None = None
    duration_ms: int = 0
    # Transport error (connection refused, TLS failure, timeout), if the request did not get a response.
    error: str | None = None
    started_at_utc: datetime | None = None

    def to_dict(self):
        return _clean({
            "method": self.method,
            "url": self.url,
            "requestHeaders": _pairs(self.request_headers),
            "requestBody": self.request_body,
            "statusCode": self.status_code,
            "responseHeaders": _pairs(self.response_headers),
            "responseBody": self.response_body,
            "durationMs": self.duration_ms,
            "error": self.error,
            "startedAtUtc": format_time(self.started_at_utc),
        })

    @staticmethod
    def from_dict(d):
        return HttpExchange(
            method=d.get("method", ""), url=d.get("url", ""),
            request_headers=_unpairs(d.get("requestHeaders")), request_body=d.get("requestBody"),
            status_code=d.get("statusCode"), response_headers=_unpairs(d.get("responseHeaders")),
            response_body=d.get("responseBody"), duration_ms=d.get("durationMs", 0), error=d.get("error"),
            started_at_utc=parse_time(d.get("startedAtUtc")))


@dataclass
class StepResult:
    name: str
    kind: str
    outcome: Outcome = Outcome.PASSED
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Facts the step observed, e.g. negotiated TLS protocol or certificate expiry.
    info: list[str] = field(default_factory=list)
    exchange: HttpExchange | None = None
    # Further calls made by the step (e.g. discovery and token requests during SCA).
    exchanges: list[HttpExchange] = field(default_factory=list)
    duration_ms: int = 0
    # The step could not be completed because of something on our side (not serialized).
    is_tool_error: bool = False

    def all_exchanges(self) -> list[HttpExchange]:
        return ([self.exchange] if self.exchange else []) + self.exchanges

    def to_dict(self):
        return _clean({
            "name": self.name,
            "kind": self.kind,
            "outcome": self.outcome.value,
            "failures": self.failures,
            "warnings": self.warnings,
            "info": self.info,
            "exchange": self.exchange.to_dict() if self.exchange else None,
            "exchanges": [x.to_dict() for x in self.exchanges],
            "durationMs": self.duration_ms,
        })

    @staticmethod
    def from_dict(d):
        return StepResult(
            name=d.get("name", ""), kind=d.get("kind", ""), outcome=Outcome(d.get("outcome", "Passed")),
            failures=list(d.get("failures") or []), warnings=list(d.get("warnings") or []), info=list(d.get("info") or []),
            exchange=HttpExchange.from_dict(d["exchange"]) if d.get("exchange") else None,
            exchanges=[HttpExchange.from_dict(x) for x in d.get("exchanges") or []],
            duration_ms=d.get("durationMs", 0))


@dataclass
class ScenarioResult:
    id: str
    title: str
    suite: str
    severity: str
    spec_ref: str | None = None
    origin: str | None = None
    source: str | None = None
    outcome: Outcome = Outcome.PASSED
    # Short explanation for non-passed outcomes.
    reason: str | None = None
    steps: list[StepResult] = field(default_factory=list)
    cleanup: list[StepResult] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def all_warnings(self) -> list[str]:
        return [w for s in self.steps + self.cleanup for w in s.warnings]

    def to_dict(self):
        return _clean({
            "id": self.id,
            "title": self.title,
            "suite": self.suite,
            "severity": self.severity,
            "specRef": self.spec_ref,
            "origin": self.origin,
            "source": self.source,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "steps": [s.to_dict() for s in self.steps],
            "cleanup": [s.to_dict() for s in self.cleanup],
            "durationMs": self.duration_ms,
            "allWarnings": self.all_warnings,
        })

    @staticmethod
    def from_dict(d):
        return ScenarioResult(
            id=d.get("id", ""), title=d.get("title", ""), suite=d.get("suite", ""), severity=d.get("severity", ""),
            spec_ref=d.get("specRef"), origin=d.get("origin"), source=d.get("source"),
            outcome=Outcome(d.get("outcome", "Passed")), reason=d.get("reason"),
            steps=[StepResult.from_dict(s) for s in d.get("steps") or []],
            cleanup=[StepResult.from_dict(s) for s in d.get("cleanup") or []],
            duration_ms=d.get("durationMs", 0))


def pass_rate(scenarios) -> float:
    """Share of executed scenarios (passed + failed) that passed, in percent."""
    passed = sum(s.outcome == Outcome.PASSED for s in scenarios)
    executed = passed + sum(s.outcome == Outcome.FAILED for s in scenarios)
    return 0 if executed == 0 else round(100.0 * passed / executed, 1)


def format_rate(rate: float) -> str:
    """Like .NET's "0.#" format: 100 -> "100", 88.89 -> "88.9"."""
    text = f"{rate:.1f}"
    return text[:-2] if text.endswith(".0") else text


@dataclass
class RunResult:
    tool_version: str
    bank_bic: str
    bank_name: str
    xs2a_url: str
    tpp_id: str | None = None
    started_at_utc: datetime | None = None
    finished_at_utc: datetime | None = None
    scenarios: list[ScenarioResult] = field(default_factory=list)

    def count(self, outcome: Outcome) -> int:
        return sum(s.outcome == outcome for s in self.scenarios)

    @property
    def pass_rate(self) -> float:
        return pass_rate(self.scenarios)

    def to_dict(self):
        rate = self.pass_rate
        return _clean({
            "toolVersion": self.tool_version,
            "bankBic": self.bank_bic,
            "bankName": self.bank_name,
            "xs2aUrl": self.xs2a_url,
            "tppId": self.tpp_id,
            "startedAtUtc": format_time(self.started_at_utc),
            "finishedAtUtc": format_time(self.finished_at_utc),
            "scenarios": [s.to_dict() for s in self.scenarios],
            "passRate": int(rate) if float(rate).is_integer() else rate,
        })

    @staticmethod
    def from_dict(d):
        return RunResult(
            tool_version=d.get("toolVersion", ""), bank_bic=d.get("bankBic", ""), bank_name=d.get("bankName", ""),
            xs2a_url=d.get("xs2aUrl", ""), tpp_id=d.get("tppId"),
            started_at_utc=parse_time(d.get("startedAtUtc")), finished_at_utc=parse_time(d.get("finishedAtUtc")),
            scenarios=[ScenarioResult.from_dict(s) for s in d.get("scenarios") or []])
