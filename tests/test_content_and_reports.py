import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from aqt import profiles, reporting, scenarios
from aqt.oauth import OAuthQuirks, build_authorize_url, try_build_approval
from aqt.results import HttpExchange, Outcome, RunResult, ScenarioResult, StepResult
from aqt.scenarios import TemplateLibrary
from conftest import BANKS, SCENARIOS


def test_every_profile_and_scenario_in_the_repository_loads():
    for file in profiles.list_profiles(BANKS):
        profiles.load_file(file)
    loaded = scenarios.load_all(SCENARIOS)
    assert len(loaded) >= 23
    templates = TemplateLibrary.load(SCENARIOS)
    names = {n.lower() for n in templates.header_set_names}
    for s in loaded:
        for step in s.steps + (s.cleanup or []):
            if step.request and step.request.headers_template:
                assert step.request.headers_template.lower() in names


def test_profile_extends_merges_the_shared_base():
    p = profiles.load("CRTUGE22", BANKS)
    assert p.tpp.qwac.password_env == "AQT_QWAC_PASSWORD"  # from _shared.yaml
    assert p.limits.delay_between_requests_ms == 200


def test_filter_is_case_insensitive():
    loaded = scenarios.load_all(SCENARIOS)
    assert {s.suite for s in scenarios.filter_scenarios(loaded, ["PREFLIGHT"], [])} == {"preflight"}
    assert [s.id for s in scenarios.filter_scenarios(loaded, [], ["ais-001"])] == ["AIS-001"]


def _sample_run() -> RunResult:
    x = HttpExchange("GET", "https://bank/x", [("X-Request-ID", "1")], None, 200, [("Content-Type", "application/json")],
                     '{"a":1}', 12, None, datetime(2026, 1, 1, tzinfo=timezone.utc))
    ok = ScenarioResult("A-1", "First", "preflight", "Critical", outcome=Outcome.PASSED,
                        steps=[StepResult("s", "request", Outcome.PASSED, warnings=["slow"], exchange=x, duration_ms=12)])
    bad = ScenarioResult("B-1", "Second <b>", "negative", "Major", outcome=Outcome.FAILED, reason="s: Status 500",
                         steps=[StepResult("s", "request", Outcome.FAILED, failures=["Status 500"])])
    skip = ScenarioResult("C-1", "Third", "tbc", "Minor", outcome=Outcome.SKIPPED, reason="Profile does not provide 'x'")
    return RunResult("0.1.0", "BIC", "Bank", "https://bank/", "TPP", datetime(2026, 1, 1, tzinfo=timezone.utc),
                     datetime(2026, 1, 1, 0, 1, tzinfo=timezone.utc), [ok, bad, skip])


def test_summary_json_round_trips_in_the_csharp_shape():
    run = _sample_run()
    data = json.loads(reporting.summary_json(run))
    assert data["passRate"] == 50 and data["scenarios"][0]["steps"][0]["exchange"]["requestHeaders"] == [{"key": "X-Request-ID", "value": "1"}]
    assert "reason" not in data["scenarios"][0]  # nulls are omitted
    assert RunResult.from_dict(data).to_dict() == data


def test_junit_and_html():
    run = _sample_run()
    root = ET.fromstring(reporting.junit(run))
    assert root.get("tests") == "3" and root.get("failures") == "1" and root.get("skipped") == "1"
    assert root.find(".//testcase/system-out").text == "WARNING: slow"
    page = reporting.html_report(run)
    assert "Second &lt;b&gt;" in page and '<td class="o-Failed">Failed</td>' in page


def test_authorize_url_and_headless_approval():
    url = build_authorize_url("http://h/oauth/authorize?x=1", "TPP", "http://localhost:5199/callback", "AIS:1", "st", "ch",
                              OAuthQuirks(" openid", "123", "ka", {"prompt": "login"}))
    assert url == ("http://h/oauth/authorize?x=1&redirect_uri=http%3A%2F%2Flocalhost%3A5199%2Fcallback&response_type=code"
                   "&client_id=TPP&code_challenge=ch&code_challenge_method=S256&state=st&scope=AIS%3A1%20openid"
                   "&nonce=123&ui_locales=ka&prompt=login")

    page = ('<form method="post" action="/oauth/authorize"><input type=hidden name="state" value="a&amp;b">'
            '<button name="decision" value="approve">Approve</button><button name="decision" value="deny">Deny</button></form>')
    method, target, body = try_build_approval(page, "http://h/oauth/authorize?q")
    assert (method, target, body) == ("POST", "http://h/oauth/authorize", b"state=a%26b&decision=approve")
    assert try_build_approval(page.replace("<button", '<input type="password"><button', 1), "http://h/") is None
