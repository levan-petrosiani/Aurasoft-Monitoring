"""Cross-bank comparison and latency benchmark (aqt/benchmark.py): operations, percentiles, reproducibility, output."""

import json
from datetime import datetime, timedelta, timezone

from aqt import benchmark
from aqt.results import HttpExchange, Outcome, RunResult, ScenarioResult, StepResult

from conftest import REPO_ROOT

START = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)


def test_operations_match_across_banks_whose_urls_differ():
    assert benchmark.operation("get", "https://tbc.example/0.8/v1/accounts/acc-gel-001/balances?withBalance=true") == \
        "GET /accounts/{id}/balances"
    assert benchmark.operation("GET", "https://localhost:7443/v2/accounts/3dc3d5b3-7023-4848-9853-f5400a64e80f/balances") == \
        "GET /accounts/{id}/balances"
    assert benchmark.operation("POST", "https://tbc.example/openbanking/oauth/token") == "POST /oauth/token"
    assert benchmark.operation("POST", "https://localhost:7443/v1/payments/sepa-credit-transfers") == "POST /payments/sepa-credit-transfers"


def test_nearest_rank_percentiles():
    values = list(range(1, 101))
    assert benchmark.percentile(values, 50) == 50 and benchmark.percentile(values, 95) == 95
    assert benchmark.percentile([7], 95) == 7 and benchmark.percentile([300, 100, 200], 50) == 200


def _run(bic, minutes, outcomes, durations, status=200):
    scenarios = []
    for sid, outcome in outcomes.items():
        exchanges = [HttpExchange("GET", f"https://{bic}.example/v1/accounts/acc-{i}", [], status_code=status, duration_ms=d)
                     for i, d in enumerate(durations)]
        step = StepResult("read accounts", "request", outcome, failures=["expected 200, got 404"] if outcome == Outcome.FAILED else [],
                          warnings=["Slow response: 4000 ms (limit 3000 ms)"] if max(durations) > 3000 else [],
                          exchange=exchanges[0], exchanges=exchanges[1:])
        scenarios.append(ScenarioResult(sid, f"title {sid}", "ais", "Major", spec_ref="GE-XS2A 9.1", outcome=outcome, steps=[step]))
    return benchmark.SavedRun(f"{bic}-{minutes}", RunResult("1", bic, bic.title(), "", started_at_utc=START + timedelta(minutes=minutes),
                                                            scenarios=scenarios))


def test_compare_uses_the_last_runs_and_flags_deviations_and_flaky_scenarios():
    runs = [_run("BANKAGE22", m, {"AIS-001": Outcome.PASSED, "AIS-002": o}, [100, 200, 300])
            for m, o in [(1, Outcome.PASSED), (2, Outcome.FAILED), (3, Outcome.PASSED), (4, Outcome.FAILED)]]
    runs += [_run("BANKBGE22", 1, {"AIS-001": Outcome.PASSED}, [1000, 5000], status=503)]
    runs += [_run("BANKAGE22", 0, {"AIS-001": Outcome.FAILED}, [9999])]  # older than the last 4: ignored
    c = benchmark.compare(runs, last=4)

    a, b = c.bank("BANKAGE22"), c.bank("BANKBGE22")
    assert a.runs == ["BANKAGE22-4", "BANKAGE22-3", "BANKAGE22-2", "BANKAGE22-1"]
    assert (a.passed, a.failed, a.pass_rate) == (1, 1, 50.0) and a.latency.p50 == 200 and a.latency.max == 300
    assert [(d.scenario_id, d.spec_ref, d.detail) for d in a.deviations] == [("AIS-002", "GE-XS2A 9.1", "read accounts: expected 200, got 404")]
    assert b.latency.errors == 2 and b.slow == 1
    cells = {sid: row for sid, _, row in c.scenarios}
    assert cells["AIS-001"]["BANKAGE22"].stable and (cells["AIS-001"]["BANKAGE22"].passed, cells["AIS-001"]["BANKAGE22"].executed) == (4, 4)
    assert not cells["AIS-002"]["BANKAGE22"].stable and "BANKBGE22" not in cells["AIS-002"]
    assert c.operations == ["GET /accounts/{id}"]  # measured at both banks

    text = benchmark.render_text(c)
    assert "BANKAGE22" in text and "AIS-002" in text and "passed 2 of 4" in text
    assert benchmark.compare(runs, ["bankbge22"]).banks[0].bic == "BANKBGE22"
    chart = benchmark.latency_chart(c)
    assert chart.startswith("<svg") and "p50 200 ms" in chart and 'class="bank-1"' in chart


def test_cli_and_portal_read_saved_reports(tmp_path, capsys):
    from aqt.cli import main
    from aqt.portal.app import create_app
    run = _run("BANKAGE22", 1, {"AIS-001": Outcome.PASSED}, [120, 180]).result
    (tmp_path / "reports" / "BANKAGE22-1").mkdir(parents=True)
    (tmp_path / "reports" / "BANKAGE22-1" / "summary.json").write_text(json.dumps(run.to_dict()), encoding="utf-8")

    assert main(["compare", "--out", str(tmp_path / "reports")]) == 0
    assert "GET /accounts/{id}" in capsys.readouterr().out
    assert main(["compare", "--out", str(tmp_path / "reports"), "--bank", "NOPE"]) == 2

    app = create_app(REPO_ROOT, str(tmp_path / "portal"), use_database=False)
    page = app.test_client().get("/compare").get_data(as_text=True)
    assert "Compare banks" in page
