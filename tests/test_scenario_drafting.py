"""Scenario drafting (Phase 3): code-owned fields, checks, MockBank dry runs, review and coverage."""

import os
import shutil
from datetime import datetime, timezone

import pytest

from aqt import scenarios as engine
from aqt.errors import ConfigurationError
from aqt.store.entities import RuleRow

from conftest import BANKS, SCENARIOS

pytest.importorskip("psycopg")

from aqt.ai.scenario_drafting import (Identity, ScenarioDrafter, ScenarioReview, ScenarioReviewError,  # noqa: E402
                                      apply_identity, check_scenario, expectation_notes, extract, next_scenario_id,
                                      suite_and_prefix)

from ai_support import ScriptedChat, ThrowawayDatabase, require_database  # noqa: E402

FREQUENCY_SCENARIO = """```yaml
id: WHATEVER-1
rules: [XX-YY-999]
title: Consent with frequencyPerDay above 4 is rejected
description: Sends frequencyPerDay 7.
steps:
  - name: POST consent with frequencyPerDay = 7
    request:
      method: POST
      path: v2/consents/account-access
      headersTemplate: consent
      bodyTemplate: consent.aspspManaged
      body: { frequencyPerDay: 7 }
    expect: { status: 400, errorCode: FORMAT_ERROR, echoRequestId: true }
```"""

# MockBank accepts a 30-day consent; a scenario expecting it to be rejected fails on an expectation (a "gap" claim).
LONG_CONSENT = FREQUENCY_SCENARIO.replace("{ frequencyPerDay: 7 }", "{ validTo: '{{date.today+30d}}' }") \
                                 .replace("FORMAT_ERROR", "PERIOD_INVALID")


def rule(**overrides) -> RuleRow:
    now = datetime.now(timezone.utc)
    values = dict(id="TS-CON-001", status="draft", title="Frequency limit", level="MUST", service="AIS", area="CON",
                  requirement="The ASPSP rejects frequencyPerDay above 4 with 400 FORMAT_ERROR.", standard="TS-IG",
                  version="1.0", clause_number="6.3", clause_key="TS-IG:1.0:6.3", quote="shall reject", drafted_by="ai",
                  created_utc=now, updated_utc=now,
                  check_json={"kind": "negative", "description": "d", "expectStatus": [400], "expectErrorCodes": ["FORMAT_ERROR"]})
    values.update(overrides)
    return RuleRow(**values)


# ---- without a database ----------------------------------------------------------------------------------------

def test_answer_parts_are_extracted():
    body, gap = extract("<think>hmm</think>MOCK_GAP: the mock does not check it\n" + FREQUENCY_SCENARIO)
    assert body.startswith("id: WHATEVER-1") and gap == "the mock does not check it"
    assert extract("title: x\nsteps: []")[1] is None


def test_code_owns_the_fields_that_link_a_scenario_to_the_standard():
    tree = engine.yamljson.parse(extract(FREQUENCY_SCENARIO)[0], "t")
    out = apply_identity(tree, Identity("NEG-CON-010", "negative", "Major", "TS-IG 1.0 6.3", "ai:x from rule TS-CON-001", "TS-CON-001"))
    assert (out["id"], out["rules"], out["suite"]) == ("NEG-CON-010", ["TS-CON-001"], "negative")
    assert list(out)[:7] == ["id", "title", "suite", "severity", "specRef", "origin", "rules"]


def test_ids_and_suites():
    assert suite_and_prefix(rule()) == ("negative", "NEG-CON")
    assert suite_and_prefix(rule(check_json={"kind": "positive"}, service="PIS")) == ("pis", "PIS")
    assert suite_and_prefix(rule(check_json=None, service="COMMON")) == ("common", "COM")
    assert next_scenario_id("NEG-CON", {"neg-con-001", "neg-con-008", "neg-sig-002"}) == "NEG-CON-009"
    assert next_scenario_id("AIS", set()) == "AIS-001"


def test_drafts_must_only_talk_to_the_banks_xs2a_api():
    templates = engine.TemplateLibrary.load(SCENARIOS)
    good = "id: X-001\ntitle: t\nsuite: s\nsteps:\n  - request: { method: GET, path: v2/accounts, headersTemplate: basic }\n"
    assert check_scenario(good, "x", templates).id == "X-001"
    for bad in (good.replace("v2/accounts", "https://evil.example/x"),
                good.replace("headersTemplate: basic", "headersTemplate: nope"),
                good.replace("headersTemplate: basic", "headersTemplate: basic, target: absolute")):
        with pytest.raises(ConfigurationError):
            check_scenario(bad, "x", templates)


def test_expectations_are_compared_with_the_rule():
    scenario = engine.load_text(extract(FREQUENCY_SCENARIO)[0].replace("XX-YY-999", "TS-CON-001") + "suite: negative\n", "x")
    assert expectation_notes(scenario, rule()) == []
    assert len(expectation_notes(scenario, rule(check_json={"expectStatus": [403], "expectErrorCodes": ["PERIOD_INVALID"]}))) == 2


# ---- with a database and an in-process MockBank -----------------------------------------------------------------

@pytest.fixture()
def env(tmp_path):
    require_database()
    from aqt.ai import options as opt
    from aqt.ai.chat import ChatFactory, InMemoryAuditSink
    from aqt.ai.rules import RuleBook
    from aqt.dryrun import MockDryRun
    db = ThrowawayDatabase()
    scenarios_dir = str(tmp_path / "scenarios")
    shutil.copytree(SCENARIOS, scenarios_dir)
    db.store.add_rules([rule()], [])
    chat = ScriptedChat([])
    factory = ChatFactory(opt.AiOptions(), InMemoryAuditSink(), db.store.masker, provider_override=lambda s: chat)
    with MockDryRun(BANKS, scenarios_dir) as dry_run:
        yield db.store, chat, ScenarioDrafter(db.store, factory, scenarios_dir, dry_run), scenarios_dir, RuleBook(db.store), dry_run
    db.drop()


def test_only_approved_rules_are_drafted(env):
    store, chat, drafter, *_ = env
    with pytest.raises(ValueError, match="approved"):
        drafter.draft("TS-CON-001")
    assert chat.prompts == []


def test_a_draft_is_fixed_from_engine_errors_and_passes_on_mockbank(env):
    store, chat, drafter, _, book, _ = env
    book.approve("TS-CON-001", "Ana")
    chat.answers += [FREQUENCY_SCENARIO.replace("consent.aspspManaged", "consent.missing"), FREQUENCY_SCENARIO]
    draft = drafter.draft("TS-CON-001", "test")

    assert (draft.status, draft.attempts, draft.dry_run_outcome, draft.scenario_id) == ("draft", 2, "Passed", "NEG-CON-009")
    assert "unknown bodyTemplate 'consent.missing'" in chat.prompts[1][1][-1]["content"]
    assert "rules: [TS-CON-001]" in draft.yaml and "WHATEVER" not in draft.yaml
    assert store.get_scenario_draft(draft.id).yaml == draft.yaml
    assert "frequencyPerDay" in chat.prompts[0][0] and "TS-CON-001" in chat.prompts[0][1][0]["content"]


def test_a_mockbank_gap_is_recorded_for_the_reviewer(env):
    store, chat, drafter, _, book, _ = env
    book.approve("TS-CON-001", "Ana")
    chat.answers += [LONG_CONSENT, "MOCK_GAP: MockBank does not check the limit\n" + LONG_CONSENT]
    draft = drafter.draft("TS-CON-001")
    assert (draft.status, draft.dry_run_outcome) == ("draft", "Failed")
    assert draft.mock_gap == "MockBank does not check the limit"
    assert "Status 201, expected 400" in chat.prompts[1][1][-1]["content"]
    assert any("PERIOD_INVALID" not in n and "FORMAT_ERROR" in n for n in draft.check_notes)  # rule expects FORMAT_ERROR


def test_drafting_gives_up_after_three_attempts(env):
    store, chat, drafter, _, book, _ = env
    book.approve("TS-CON-001", "Ana")
    chat.answers += ["no yaml here", "steps: nope", "title: t"]
    draft = drafter.draft("TS-CON-001")
    assert (draft.status, draft.attempts) == ("failed", 3)


def test_review_writes_the_file_and_links_the_rule(env):
    store, chat, drafter, scenarios_dir, book, dry_run = env
    book.approve("TS-CON-001", "Ana")
    chat.answers += [FREQUENCY_SCENARIO]
    draft = drafter.draft("TS-CON-001")
    review = ScenarioReview(store, scenarios_dir, dry_run)

    with pytest.raises(ScenarioReviewError):
        review.approve(draft.id, "")
    with pytest.raises(ScenarioReviewError):
        review.reject(draft.id, "Ana", "")

    edited = review.edit(draft.id, "Ana", draft.yaml.replace("frequencyPerDay above 4", "frequencyPerDay 7").replace("id: NEG-CON-009", "id: HACKED-1"))
    assert edited.scenario_id == "NEG-CON-009" and "id: NEG-CON-009" in edited.yaml and edited.dry_run_outcome == "Passed"

    approved = review.approve(draft.id, "Ana", "looks right")
    path = os.path.join(os.path.dirname(scenarios_dir), approved.file_path)
    assert approved.status == "approved" and os.path.isfile(path)
    assert next(s for s in engine.load_all(scenarios_dir) if s.id == "NEG-CON-009").rules == ["TS-CON-001"]
    assert store.get_rule("TS-CON-001").scenarios == ["NEG-CON-009"]
    assert [h.action for h in store.get_rule_history("TS-CON-001")] == ["approved", "linked"]
    with pytest.raises(ScenarioReviewError):
        review.approve(draft.id, "Ana")

    from aqt.store import coverage
    result = coverage.compute(store, scenarios_dir)
    assert (result.approved_count, result.covered_count, result.problems) == (1, 1, [])
    book.reopen("TS-CON-001", "Ana")
    assert coverage.compute(store, scenarios_dir).problems == ["NEG-CON-009: rule TS-CON-001 is draft, not approved"]
