"""Alerts, computed in code after a run is saved: what changed against the bank's previous run. Notifications go to an
optional webhook (masked). Never raises into the caller: alerting must not affect a test run."""

import json
import urllib.request
import uuid
from datetime import datetime, timezone

from ..store.entities import AlertRow, RunRow
from .config import MonitoringConfig

_PROBLEM = ("Failed", "Error")


def detect(run: RunRow, previous: RunRow | None) -> list[AlertRow]:
    now = datetime.now(timezone.utc)

    def alert(kind, message, scenario_id=None, before=None, after=None):
        return AlertRow(id=uuid.uuid4(), created_utc=now, run_id=run.id, bank_bic=run.bank_bic, kind=kind, message=message,
                        previous_run_id=previous.id if previous else None, scenario_id=scenario_id, before=before, after=after)

    alerts = []
    if run.status == "Failed":
        alerts.append(alert("run_failed", f"{run.bank_bic}: run {run.id} failed: {run.error or 'no detail'}"))
        return alerts

    def critical_preflight_problems(r: RunRow | None) -> list:
        return [] if r is None else [s for s in r.scenarios if s.suite.lower() == "preflight" and s.severity == "Critical"
                                     and s.outcome in _PROBLEM]

    down = critical_preflight_problems(run)
    if down and not critical_preflight_problems(previous):
        first = down[0]
        alerts.append(alert("bank_down", f"{run.bank_bic}: critical preflight {first.scenario_id} {first.outcome.lower()} "
                                         f"in run {run.id}: {first.reason or ''}".rstrip(": "), first.scenario_id, None, first.outcome))

    if previous is not None:
        before = {s.scenario_id.lower(): s for s in previous.scenarios}
        reported = {a.scenario_id for a in alerts}  # a bank_down scenario is not also reported as a regression
        for s in run.scenarios:
            b = before.get(s.scenario_id.lower())
            if b is None or s.scenario_id in reported:
                continue
            if b.outcome == "Passed" and s.outcome in _PROBLEM:
                alerts.append(alert("regression", f"{run.bank_bic}: {s.scenario_id} regressed Passed → {s.outcome} in run {run.id}"
                                                  + (f": {s.reason}" if s.reason else ""), s.scenario_id, b.outcome, s.outcome))
            elif b.outcome in _PROBLEM and s.outcome == "Passed":
                alerts.append(alert("fixed", f"{run.bank_bic}: {s.scenario_id} passes again in run {run.id} (was {b.outcome})",
                                    s.scenario_id, b.outcome, s.outcome))
    return alerts


def notify(alerts: list[AlertRow], config: MonitoringConfig, masker, post=None) -> list[tuple[AlertRow, str | None]]:
    """Posts the alerts of the configured kinds to the webhook, one message; returns (alert, error or None) for each sent."""
    url = config.webhook_url
    chosen = [a for a in alerts if a.kind in config.alerts.notify]
    if not url or not chosen:
        return []
    icons = {"regression": "🔴", "bank_down": "🚨", "run_failed": "⚠️", "fixed": "✅"}
    text = "\n".join(f"{icons.get(a.kind, '•')} {a.message}" for a in chosen)
    text = masker.mask_body(text) or ""
    try:
        (post or _post)(url, {"text": "ApiTestTool monitoring\n" + text})
        return [(a, None) for a in chosen]
    except Exception as ex:  # noqa: BLE001 - recorded on the alert rows
        return [(a, f"{type(ex).__name__}: {ex}") for a in chosen]


def _post(url: str, payload: dict):
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        response.read()


def process_run(store, run_id: str, config: MonitoringConfig, post=None, warn=print) -> list[AlertRow]:
    """Detects, stores and notifies the alerts of one saved run. Returns the new alerts; problems are only reported."""
    try:
        run = store.get_run(run_id)
        if run is None or run.status not in ("Completed", "Failed"):
            return []
        added = store.add_alerts(detect(run, store.get_previous_run(run) if run.status == "Completed" else None))
        now = datetime.now(timezone.utc)
        for alert, error in notify(added, config, store.masker, post):
            alert.notified_utc, alert.notify_error = (None, error) if error else (now, None)
            store.update_alert_notification(alert.id, alert.notified_utc, alert.notify_error)
        return added
    except Exception as ex:  # noqa: BLE001 - alerting never affects the run
        if warn:
            warn(f"Monitoring: alerts for run {run_id} could not be processed: {type(ex).__name__}: {ex}")
        return []
