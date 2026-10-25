"""Turns the engine's RunResult into store rows, masking and capping exchanges on the way."""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..results import HttpExchange, Outcome, RunResult
from .entities import ExchangeRow, RunRow, ScenarioRow, StepRow
from .masking import SensitiveDataMasker, cap


@dataclass
class RunInfo:
    """What is known about a run besides its results (who started it, how, where the reports are)."""
    id: str
    source: str                  # cli | portal
    status: str                  # Queued | Running | Completed | Failed | Cancelled
    bank_bic: str
    bank_name: str | None = None
    created_utc: datetime | None = None
    started_utc: datetime | None = None
    finished_utc: datetime | None = None
    suites: list[str] = field(default_factory=list)
    error: str | None = None
    report_directory: str | None = None
    options_json: dict | None = None


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def to_rows(info: RunInfo, result: RunResult | None, masker: SensitiveDataMasker, max_body_chars: int) -> RunRow:
    run = RunRow(
        id=info.id, source=info.source, status=info.status, bank_bic=info.bank_bic,
        bank_name=info.bank_name or (result.bank_name if result else None),
        xs2a_url=result.xs2a_url if result else None, tpp_id=result.tpp_id if result else None,
        tool_version=result.tool_version if result else None,
        created_utc=_utc(info.created_utc) or datetime.now(timezone.utc),
        started_utc=_utc(info.started_utc or (result.started_at_utc if result else None)),
        finished_utc=_utc(info.finished_utc or (result.finished_at_utc if result else None)),
        suites=list(info.suites), error=info.error, report_directory=info.report_directory,
        options_json=info.options_json)

    if result is None:
        return run

    run.passed, run.failed, run.errors = result.count(Outcome.PASSED), result.count(Outcome.FAILED), result.count(Outcome.ERROR)
    run.blocked, run.skipped, run.pass_rate = result.count(Outcome.BLOCKED), result.count(Outcome.SKIPPED), result.pass_rate

    for ordinal, s in enumerate(result.scenarios):
        scenario = ScenarioRow(run_id=info.id, ordinal=ordinal, scenario_id=s.id, title=s.title, suite=s.suite,
                               severity=s.severity, outcome=s.outcome.value, spec_ref=s.spec_ref, origin=s.origin,
                               reason=s.reason, duration_ms=s.duration_ms)
        steps = [(st, False) for st in s.steps] + [(st, True) for st in s.cleanup]
        for step_ordinal, (step, is_cleanup) in enumerate(steps):
            row = StepRow(run_id=info.id, ordinal=step_ordinal, is_cleanup=is_cleanup, name=step.name, kind=step.kind,
                          outcome=step.outcome.value, failures=list(step.failures), warnings=list(step.warnings),
                          info=list(step.info), duration_ms=step.duration_ms)
            for x_ordinal, exchange in enumerate(step.all_exchanges()):
                row.exchanges.append(_exchange(info.id, x_ordinal, exchange, masker, max_body_chars))
            scenario.steps.append(row)
        run.scenarios.append(scenario)
    return run


def _exchange(run_id: str, ordinal: int, x: HttpExchange, masker: SensitiveDataMasker, max_body_chars: int) -> ExchangeRow:
    request_body, request_cut = cap(masker.mask_body(x.request_body), max_body_chars)
    response_body, response_cut = cap(masker.mask_body(x.response_body), max_body_chars)
    return ExchangeRow(
        run_id=run_id, ordinal=ordinal, method=x.method, url=masker.mask_url(x.url), status_code=x.status_code,
        duration_ms=x.duration_ms, error=x.error, started_utc=_utc(x.started_at_utc) or datetime.now(timezone.utc),
        request_headers=[[k, v] for k, v in masker.mask_headers(x.request_headers)], request_body=request_body,
        response_headers=[[k, v] for k, v in masker.mask_headers(x.response_headers)], response_body=response_body,
        truncated=request_cut or response_cut)
