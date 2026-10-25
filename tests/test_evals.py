"""The eval sets: the shipped case files are well formed, and the runner scores answers in code (scripted model)."""

import json
import os
import re

import pytest
import yaml

from conftest import REPO_ROOT

EVALS = os.path.join(REPO_ROOT, "evals")


def load(name):
    with open(os.path.join(EVALS, name), encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---- the case files ------------------------------------------------------------------------------------------------

def test_every_feature_has_cases_with_unique_names_and_expectations():
    from aqt.ai.evals import FEATURES, FILES
    for feature in FEATURES:
        cases = load(FILES[feature])["cases"]
        names = [c["name"] for c in cases]
        assert len(cases) >= 4 and len(names) == len(set(names)), feature
        assert all(c.get("expect") for c in cases), feature


def test_summary_cases_reference_their_own_runs():
    for case in load("summaries.yaml")["cases"]:
        ids = {r["id"] for r in case["runs"]}
        scenarios = {s["id"] for r in case["runs"] if r["id"] == case["run"] for s in r["scenarios"]}
        assert case["run"] in ids, case["name"]
        named = {s for f in case["expect"].get("findings") or [] for s in f["scenariosAny"]} | set(case["expect"].get("notFindings") or [])
        assert named <= scenarios, (case["name"], named - scenarios)


def test_rule_cases_are_verbatim_quotes_of_real_clauses_and_scenario_rules_have_ids():
    from aqt.scenarios import _RULE_ID
    for case in load("rules.yaml")["cases"]:
        low, high = case["expect"]["rules"]
        assert 0 <= low <= high and case["clause"]["text"].strip()
    for case in load("scenarios.yaml")["cases"]:
        assert _RULE_ID.match(case["rule"]["id"]) and case["rule"]["check"]["expectStatus"]


def test_ask_questions_expect_facts_that_are_in_the_dataset():
    spec = load("ask.yaml")
    dataset = json.dumps(spec["dataset"], ensure_ascii=False).lower()
    for case in spec["cases"]:
        for group in case["expect"]["contains"]:
            ids = [a for a in group if re.match(r"^[A-Z]+[A-Z0-9_-]*\d", a)]
            assert not ids or any(a.lower() in dataset for a in ids), (case["name"], group)


# ---- scoring (database + scripted model) -----------------------------------------------------------------------------

class Script:
    provider, model = "fake", "scripted"

    def __init__(self, answers):
        self.answers = list(answers)

    def complete(self, system, messages, json_schema=None, tools=None):
        from aqt.ai.chat import ChatResult
        answer = self.answers.pop(0)
        return answer if isinstance(answer, ChatResult) else ChatResult(answer, "scripted", "stop", 10, 5)


@pytest.fixture()
def runner_for():
    from ai_support import TEST_DB, bag_of_words, require_database
    require_database()
    from aqt.ai import options as opt
    from aqt.ai.evals import EvalRunner

    def make(answers):
        model = Script(answers)
        return EvalRunner(TEST_DB, opt.AiOptions(), REPO_ROOT, progress=lambda _: None,
                          provider_override=lambda s: model, embed_override=bag_of_words)
    return make


def _summary(health, findings):
    return json.dumps({"headline": "h", "health": health, "attention": [], "findings": [
        {"title": t, "side": side, "severity": "Major", "scenarios": ids, "explanation": "e", "evidence": "ev", "specRef": "", "nextStep": "n"}
        for t, side, ids in findings]})


def test_a_right_summary_passes_and_a_wrong_one_fails_on_the_right_checks(runner_for):
    right = _summary("broken", [("Sandbox returned 503", "environment", ["PRE-003", "PRE-004"])])
    result = runner_for([right]).run(["summary"], "tbc-sandbox-outage")[0]
    assert result.passed, [(c.label, c.detail) for c in result.checks if not c.ok]

    wrong = _summary("healthy", [("Bank bug", "our-side", ["PRE-003"]), ("TLS", "our-side", ["PRE-004", "PRE-001"])])
    result = runner_for([wrong]).run(["summary"], "tbc-sandbox-outage")[0]
    failed = {c.label for c in result.checks if not c.ok}
    assert not result.passed and failed == {"health is broken or degraded or inconclusive", "… on side environment or bank",
                                            "PRE-001 is not reported as a problem"}


def test_rule_drafting_is_scored_on_topics_levels_and_quotes(runner_for):
    case = next(c for c in load("rules.yaml")["cases"] if c["name"] == "ge-consent-period")
    text = case["clause"]["text"]
    quote = text[text.index("იმ შემთხვევაში"):text.index("PERIOD_INVALID.") + len("PERIOD_INVALID")]
    answer = json.dumps({"skipped": "", "rules": [{
        "title": "Reject long consents", "level": "SHOULD", "service": "AIS", "area": "CON",
        "requirement": "The ASPSP rejects validUntil more than 90 days ahead with PERIOD_INVALID.", "quote": quote,
        "quoteEnglish": "If the value exceeds 90 days, the ASPSP must return PERIOD_INVALID.", "operation": "", "notes": "",
        "check": {"kind": "negative", "description": "d", "expectStatus": [400], "expectErrorCodes": ["PERIOD_INVALID"]}}]})
    result = runner_for([answer]).run(["rules"], "ge-consent-period")[0]
    failed = {c.label for c in result.checks if not c.ok}
    assert failed == {"… at level MUST", "a rule about 9999-12-31"}  # wrong level, second duty missing


def test_ask_answers_are_scored_on_facts_and_verified_citations(runner_for):
    from aqt.ai.chat import ChatResult, ToolCall
    lookup = ChatResult("", "scripted", "tool_use", tool_calls=[ToolCall("c1", "list_runs", {"bank": "TBCBGE22"})])
    good = ChatResult("TBCBGE22 had 3 runs in October 2026 (TBCBGE22-20261020-164442, …).", "scripted", "stop")
    result = runner_for([lookup, good]).run(["ask"], "count-runs")[0]
    assert result.passed, [(c.label, c.detail) for c in result.checks if not c.ok]

    invented = ChatResult("TBCBGE22 had 5 runs, e.g. TBCBGE22-20261099-000000.", "scripted", "stop")
    result = runner_for([lookup, invented]).run(["ask"], "count-runs")[0]
    assert {c.label for c in result.checks if not c.ok} == {"mentions 3 / three", "every cited id was looked up"}


def test_report_files(tmp_path):
    from aqt.ai.evals import CaseResult, write_report
    r = CaseResult("ask", "count-runs", "fake", "m")
    r.check("mentions 3", True)
    r.check("used a tool", False, "no tools")
    json_path, md_path = write_report([r], [], str(tmp_path))
    data = json.loads(open(json_path, encoding="utf-8").read())
    assert data["summary"][0]["checksPassed"] == 1 and data["cases"][0]["passed"] is False
    assert "❌ used a tool — no tools" in open(md_path, encoding="utf-8").read()
