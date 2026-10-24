"""The whole tool against an in-process MockBank on free ports: every suite that applies to MOCK must pass."""

import socket

import pytest

from aqt import profiles, scenarios
from aqt.context import RunContext
from aqt.mockbank import MockBankHost, MockBankOptions
from aqt.oauth import headless_authorizer
from aqt.results import Outcome
from aqt.runner import ScenarioRunner
from aqt.scenarios import TemplateLibrary
from conftest import BANKS, SCENARIOS


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock():
    options = MockBankOptions.load({"https_port": free_port(), "http_port": free_port()})
    host = MockBankHost(options, quiet=True)
    host.start()
    yield host
    host.stop()


def make_context(mock, **overrides):
    profile = profiles.load("MOCK", BANKS)
    base = mock.options.https_base_url
    profile.endpoints.xs2a = base + "/"
    profile.endpoints.discovery = base + "/.well-known/oauth-authorization-server"
    profile.tpp.redirect_uri = f"http://localhost:{free_port()}/callback"
    for key, value in overrides.items():
        setattr(profile, key, value)
    ctx = RunContext.create(profile, TemplateLibrary.load(SCENARIOS))
    errors = []
    ctx.open_browser = headless_authorizer(errors.append)
    return ctx, errors


def test_all_mock_suites_pass(mock):
    ctx, errors = make_context(mock)
    with ctx:
        run = ScenarioRunner(ctx).run(scenarios.load_all(SCENARIOS))

    problems = [(s.id, s.reason) for s in run.scenarios if s.outcome not in (Outcome.PASSED, Outcome.SKIPPED)]
    assert problems == [] and errors == []
    assert run.count(Outcome.PASSED) == 16
    assert all(s.suite == "tbc" for s in run.scenarios if s.outcome == Outcome.SKIPPED)


def test_failed_critical_preflight_blocks_the_rest(mock):
    ctx, _ = make_context(mock)
    ctx.qsealc_info = None  # pretend there is no QSealC: PRE-001 fails
    with ctx:
        run = ScenarioRunner(ctx).run(scenarios.filter_scenarios(scenarios.load_all(SCENARIOS), ["preflight", "negative"], []))

    assert next(s for s in run.scenarios if s.id == "PRE-001").outcome == Outcome.FAILED
    assert all(s.outcome == Outcome.BLOCKED for s in run.scenarios if s.suite == "negative")


def test_unreachable_bank_is_a_failure_not_a_crash(mock):
    ctx, _ = make_context(mock)
    ctx.profile.endpoints.xs2a = f"https://localhost:{free_port()}/"
    with ctx:
        result = ScenarioRunner(ctx).run_scenario(next(s for s in scenarios.load_all(SCENARIOS) if s.id == "PRE-004"))
    assert result.outcome == Outcome.FAILED
    assert "No response from localhost" in result.reason


def test_consent_period_is_limited_to_90_days(mock):
    """GE-XS2A 9.1.1.10: validTo beyond 90 days -> 400 PERIOD_INVALID; 9999-12-31 is shortened to 90 days."""
    from datetime import timedelta
    from aqt.mockbank.state import utc_today
    base = """id: T-001
title: t
suite: t
steps:
  - request:
      method: POST
      path: v2/consents/account-access
      headersTemplate: consent
      bodyTemplate: consent.accountList
      body: { validTo: "VALID_TO" }
    expect: { status: STATUS, ERROR }
"""
    too_long = scenarios.load_text(base.replace("VALID_TO", "{{date.today+91d}}").replace("STATUS", "400")
                                   .replace("ERROR", "errorCode: PERIOD_INVALID"), "t")
    at_limit = scenarios.load_text(base.replace("VALID_TO", "{{date.today+90d}}").replace("STATUS", "201").replace(", ERROR", ""), "t")
    forever = scenarios.load_text(base.replace("VALID_TO", "9999-12-31").replace("STATUS", "201").replace(", ERROR", "")
                                  + "    save: { consentId: $.consentId }\n", "t")
    ctx, _ = make_context(mock)
    with ctx:
        runner = ScenarioRunner(ctx)
        results = [runner.run_scenario(s) for s in (too_long, at_limit, forever)]
    assert [(r.outcome, r.reason) for r in results] == [(Outcome.PASSED, None)] * 3
    newest = max(mock.app.state.consents.values(), key=lambda c: c.created_utc)
    assert newest.valid_to == utc_today() + timedelta(days=90)  # the 9999-12-31 one, shortened
