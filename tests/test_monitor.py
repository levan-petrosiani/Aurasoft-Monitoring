"""Monitoring (Phase 6): monitoring.yaml, due times, alerts, notifications, trends, retention, the portal monitor."""

import os
from datetime import datetime, timedelta, timezone

import pytest

from aqt.errors import ConfigurationError
from aqt.monitor import config, trends
from aqt.monitor.alerts import detect, notify
from aqt.store.entities import RunRow, ScenarioRow
from aqt.store.masking import DEFAULT as MASKER

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)


def write(tmp_path, text):
    (tmp_path / "monitoring.yaml").write_text(text, encoding="utf-8")
    return str(tmp_path)


# ---- monitoring.yaml and due times ------------------------------------------------------------------------------

def test_no_file_means_no_schedules_and_defaults(tmp_path):
    c = config.load(str(tmp_path))
    assert c.schedules == [] and c.retention.exchanges_days == 90 and "regression" in c.alerts.notify


@pytest.mark.parametrize("schedule, problem", [
    ("{name: a, bank: MOCK}", "exactly one of"),
    ("{name: a, bank: MOCK, every: 6h, at: '02:00'}", "exactly one of"),
    ("{name: a, bank: MOCK, every: 2m}", "at least 5 minutes"),
    ("{name: a, bank: MOCK, every: weekly}", "30m, 6h or 1d"),
    ("{name: a, bank: MOCK, at: '25:00'}", "HH:MM"),
])
def test_bad_schedules_are_explained(tmp_path, schedule, problem):
    with pytest.raises(ConfigurationError, match=problem):
        config.load(write(tmp_path, f"schedules:\n  - {schedule}\n"))


def test_duplicate_names_and_unknown_alert_kinds_are_refused(tmp_path):
    with pytest.raises(ConfigurationError, match="duplicate"):
        config.load(write(tmp_path, "schedules:\n  - {name: a, bank: X, every: 1h}\n  - {name: A, bank: Y, every: 1h}\n"))
    with pytest.raises(ConfigurationError, match="unknown kind"):
        config.load(write(tmp_path, "alerts: { notify: [regression, coffee] }\n"))


def test_the_shipped_monitoring_file_loads():
    from conftest import REPO_ROOT
    c = config.load(REPO_ROOT)
    assert {s.name for s in c.schedules} >= {"mock-nightly"}


def test_every_runs_at_once_the_first_time_then_after_the_interval():
    s = config.Schedule(name="e", bank="MOCK", every="6h")
    assert config.is_due(s, None, NOW)
    assert not config.is_due(s, NOW - timedelta(hours=5), NOW) and config.is_due(s, NOW - timedelta(hours=6), NOW)
    assert not config.is_due(config.Schedule(name="off", bank="MOCK", every="6h", enabled=False), None, NOW)


def test_at_waits_for_its_time_of_day_and_catches_up_once():
    local = NOW.astimezone()
    at = (local + timedelta(hours=1)).strftime("%H:%M")
    s = config.Schedule(name="d", bank="MOCK", at=at)
    assert not config.is_due(s, None, NOW)                              # never ran: waits for the next 'at'
    assert config.is_due(s, NOW - timedelta(days=2), NOW)                # missed yesterday's: due now (once)
    assert not config.is_due(s, NOW - timedelta(minutes=1), NOW)         # ran after yesterday's time: wait for today's


# ---- alerts ------------------------------------------------------------------------------------------------------

def run(run_id, outcomes, status="Completed", error=None, preflight=None):
    r = RunRow(id=run_id, source="cli", status=status, bank_bic="TESTGE22", error=error)
    for i, (sid, outcome) in enumerate(outcomes.items()):
        r.scenarios.append(ScenarioRow(run_id=run_id, ordinal=i, scenario_id=sid, title=sid, suite="negative", severity="Major",
                                       outcome=outcome, reason=f"{sid} reason" if outcome != "Passed" else None))
    if preflight:
        r.scenarios.insert(0, ScenarioRow(run_id=run_id, ordinal=99, scenario_id="PRE-002", title="TLS", suite="preflight",
                                          severity="Critical", outcome=preflight, reason="handshake failed"))
    return r


def test_regressions_fixes_and_new_scenarios():
    before = run("R1", {"A": "Passed", "B": "Failed", "C": "Passed", "D": "Skipped"})
    after = run("R2", {"A": "Failed", "B": "Passed", "C": "Passed", "D": "Failed", "E": "Error"})
    found = {(a.kind, a.scenario_id) for a in detect(after, before)}
    assert found == {("regression", "A"), ("fixed", "B")}  # D was skipped, E is new: not a regression


def test_bank_down_is_reported_once_not_on_every_run_while_it_is_down():
    up = run("R1", {"A": "Passed"}, preflight="Passed")
    down = run("R2", {"A": "Blocked"}, preflight="Failed")
    still_down = run("R3", {"A": "Blocked"}, preflight="Failed")
    assert [a.kind for a in detect(down, up)] == ["bank_down"]
    assert detect(still_down, down) == []


def test_a_failed_run_is_an_alert():
    alerts = detect(run("R9", {}, status="Failed", error="Unknown bank profile"), None)
    assert [(a.kind, a.message) for a in alerts] == [("run_failed", "TESTGE22: run R9 failed: Unknown bank profile")]


def test_notifications_are_filtered_masked_and_failures_recorded(monkeypatch):
    alerts = detect(run("R2", {"A": "Failed", "B": "Passed"}), run("R1", {"A": "Passed", "B": "Failed"}))
    alerts[0].message += " Authorization: Bearer abc.def.ghi"
    sent = []
    monkeypatch.setenv("AQT_TEST_WEBHOOK", "https://hooks.example/x")
    c = config.MonitoringConfig(alerts=config.AlertsConfig(webhook_env="AQT_TEST_WEBHOOK", notify=["regression"]))
    result = notify(alerts, c, MASKER, post=lambda url, payload: sent.append(payload))
    assert [a.kind for a, error in result] == ["regression"] and "abc.def.ghi" not in sent[0]["text"]

    def broken(url, payload):
        raise OSError("connection refused")
    assert notify(alerts, c, MASKER, post=broken)[0][1] == "OSError: connection refused"
    monkeypatch.delenv("AQT_TEST_WEBHOOK")
    assert notify(alerts, c, MASKER, post=broken) == []  # no webhook configured: nothing sent, nothing failed


# ---- trends --------------------------------------------------------------------------------------------------------

def test_flaky_scenarios_and_sparkline():
    seq = {("B", "S1"): [(1, "Passed"), (2, "Failed"), (3, "Passed")], ("B", "S2"): [(1, "Failed"), (2, "Failed")]}
    assert [(f.scenario_id, f.changes) for f in trends.flaky(seq)] == [("S1", 2)]
    svg = trends.sparkline([100, 50, 80])
    assert svg.startswith("<svg") and "polyline" in svg and trends.sparkline([]) == ""


# ---- unattended runs ----------------------------------------------------------------------------------------------

def test_unattended_runs_skip_scenarios_that_need_a_login():
    from aqt import profiles, scenarios
    from aqt.context import RunContext
    from aqt.results import Outcome
    from aqt.runner import ScenarioRunner
    from conftest import BANKS, SCENARIOS
    ais = next(s for s in scenarios.load_all(SCENARIOS) if s.id == "AIS-001")
    with RunContext.create(profiles.load("MOCK", BANKS), scenarios.TemplateLibrary.load(SCENARIOS)) as ctx:
        runner = ScenarioRunner(ctx)
        runner.skip_sca_reason = "nobody to log in"
        result = runner.run_scenario(ais)
    assert (result.outcome, result.reason) == (Outcome.SKIPPED, "nobody to log in")


# ---- with a database ----------------------------------------------------------------------------------------------

@pytest.fixture()
def db():
    from ai_support import ThrowawayDatabase, require_database
    require_database()
    database = ThrowawayDatabase()
    yield database
    database.drop()


def _save(store, run_id, started, outcomes):
    from aqt.results import Outcome, RunResult, ScenarioResult
    from aqt.store.mapper import RunInfo
    result = RunResult(tool_version="t", bank_bic="TESTGE22", bank_name="Test", xs2a_url="https://x/", started_at_utc=started,
                       finished_at_utc=started + timedelta(seconds=5))
    for sid, outcome in outcomes.items():
        result.scenarios.append(ScenarioResult(id=sid, title=sid, suite="negative", severity="Major", outcome=Outcome(outcome)))
    store.save_run(RunInfo(id=run_id, source="cli", status="Completed", bank_bic="TESTGE22", created_utc=started,
                           started_utc=started, finished_utc=started + timedelta(seconds=5)), result)


def test_alerts_are_stored_once_acknowledged_and_visible_to_the_agent(db):
    from aqt.monitor.alerts import process_run
    _save(db.store, "R1", NOW - timedelta(hours=2), {"A": "Passed"})
    _save(db.store, "R2", NOW - timedelta(hours=1), {"A": "Failed"})
    c = config.MonitoringConfig()
    added = process_run(db.store, "R2", c, warn=None)
    assert [(a.kind, a.scenario_id, a.previous_run_id) for a in added] == [("regression", "A", "R1")]
    assert process_run(db.store, "R2", c, warn=None) == []                   # processing the same run again adds nothing
    assert db.store.agent_query("SELECT kind, scenario_id FROM alerts")[1] == [("regression", "A")]
    assert db.store.acknowledge_alerts([added[0].id], "Ana") == 1 and db.store.list_alerts(open_only=True) == []


def test_retention_deletes_old_exchanges_only(db):
    from ai_support import sample_run
    from aqt.store.mapper import RunInfo
    db.store.save_run(RunInfo(id="OLD", source="cli", status="Completed", bank_bic="TESTGE22"), sample_run())  # exchanges dated 2026-10-24
    status = db.store.status()
    assert db.store.prune_exchanges(36500) == 0
    assert db.store.prune_exchanges(0) == status.exchanges > 0               # 0 days: everything older than now
    assert db.store.status().scenarios == status.scenarios                   # results are kept


def test_the_portal_monitor_queues_due_schedules_once_and_prunes_daily(db, tmp_path):
    from types import SimpleNamespace
    from aqt.portal.ai_services import MonitorService
    root = write(tmp_path, "schedules:\n  - {name: often, bank: MOCK, every: 1h, withMock: true}\n"
                           "  - {name: off, bank: MOCK, every: 1h, enabled: false}\n")
    queued = []

    class Runs:
        def enqueue(self, request):
            queued.append(request)
            return SimpleNamespace(id=f"RUN-{len(queued)}", is_active=True, record=SimpleNamespace(status="Queued", error=None))

        def get(self, run_id):
            return SimpleNamespace(id=run_id, is_active=True, record=SimpleNamespace(status="Running", error=None))

    monitor = MonitorService(db.store, root)
    monitor.runs = Runs()
    monitor.tick()
    monitor.tick()  # still running: not queued twice
    assert [(r.schedule, r.unattended, r.auto_sca, r.use_mock_bank) for r in queued] == [("often", True, True, True)]
    state = db.store.get_monitor_state()
    assert state["often"].last_run_id == "RUN-1" and config.RETENTION_STATE in state and "off" not in state
    assert monitor.run_now("nope", "Ana") == "No schedule 'nope' in monitoring.yaml"


def test_monitoring_and_alerts_pages(db, tmp_path):
    from aqt.monitor.alerts import process_run
    from aqt.portal.app import create_app
    from conftest import REPO_ROOT
    _save(db.store, "R1", NOW - timedelta(hours=2), {"A": "Passed"})
    _save(db.store, "R2", NOW - timedelta(hours=1), {"A": "Error"})
    process_run(db.store, "R2", config.MonitoringConfig(), warn=None)
    client = create_app(REPO_ROOT, str(tmp_path / "runs"), database=db.connection).test_client()
    page = client.get("/monitoring")
    assert page.status_code == 200 and b"mock-nightly" in page.data and b"Flaky" in page.data
    page = client.get("/alerts")
    assert b"regressed Passed" in page.data
    alert_id = str(db.store.list_alerts()[0].id)
    assert "error=" in client.post("/alerts/ack", data={"id": alert_id}).headers["Location"]
    client.post("/reviewer", data={"name": "Ana", "next": "/alerts"})
    client.post("/alerts/ack", data={"id": alert_id})
    assert db.store.list_alerts()[0].acknowledged_by == "Ana"
    assert os.path.basename(client.post("/monitoring/run/nope").headers["Location"]).startswith("monitoring?message=")
