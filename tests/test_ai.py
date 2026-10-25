"""AI layer: options, the exact Claude request, audit and masking, run summaries, rule drafting and review."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("anthropic")
pytest.importorskip("psycopg")

from aqt.ai import options as opt  # noqa: E402
from aqt.ai.chat import AuditingChat, ChatFactory, InMemoryAuditSink, ProviderError  # noqa: E402
from aqt.ai.rules import (ReviewError, RuleBook, RuleDrafter, RuleEdit, id_prefix, question_level,  # noqa: E402
                          same_identifiers, to_yaml)
from aqt.ai.summaries import RunSummarizer, RunSummary, build_digest, match_spec_ref  # noqa: E402
from aqt.store import RunInfo  # noqa: E402
from aqt.store.entities import ClauseRow, RunRow, ScenarioRow, StandardDocumentRow, StepRow, ExchangeRow  # noqa: E402
from aqt.store.masking import DEFAULT as MASKER  # noqa: E402

from ai_support import ScriptedChat, ThrowawayDatabase, bag_of_words, require_database, sample_run  # noqa: E402


# ---- options ---------------------------------------------------------------------------------------------------

def test_defaults_send_drafting_to_claude_and_overrides_win():
    o = opt.AiOptions.from_environment({"AQT_AI_SUMMARY_PROVIDER": "ollama", "AQT_AI_ASK_PROVIDER": "ollama", "AQT_AI_ASK_MODEL": "gemma4:26b"})
    assert (o.for_feature(opt.RULE_DRAFTING).provider, o.for_feature(opt.RULE_DRAFTING).model) == ("anthropic", "claude-opus-5-5")
    assert (o.for_feature(opt.SUMMARY).provider, o.for_feature(opt.SUMMARY).model) == ("ollama", "qwen3:8b")
    assert o.for_feature(opt.ASK).model == "gemma4:26b"
    assert (o.for_feature(opt.SUMMARY).max_output_tokens, o.for_feature(opt.SUMMARY).effort) == (16000, "medium")
    switched = o.with_provider(opt.ASK, "anthropic")
    assert switched.for_feature(opt.ASK).model == "claude-opus-5-5"  # a configured model belongs to its provider


# ---- the exact Claude request (fake server, no key, no network) ------------------------------------------------

@pytest.fixture()
def fake_anthropic():
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            captured["body"] = json.loads(self.rfile.read(int(self.headers["content-length"])))
            captured["headers"] = {k.lower(): v for k, v in self.headers.items()}
            events = [
                ("message_start", {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                                                       "content": [], "stop_reason": None, "stop_sequence": None,
                                                                       "usage": {"input_tokens": 10, "output_tokens": 0}}}),
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "OK"}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0})] + ([
                ("content_block_start", {"type": "content_block_start", "index": 1,
                                         "content_block": {"type": "tool_use", "id": "toolu_1", "name": "list_runs", "input": {}}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                         "delta": {"type": "input_json_delta", "partial_json": '{"bank": "TBCBGE22"}'}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 1})] if captured.get("stop") == "tool_use" else []) + [
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": captured.get("stop", "end_turn"), "stop_sequence": None},
                                   "usage": {"output_tokens": 2}}),
                ("message_stop", {"type": "message_stop"})]
            payload = "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    captured["url"] = f"http://127.0.0.1:{server.server_port}"
    yield captured
    server.shutdown()


def _claude(fake, **features):
    o = opt.AiOptions()
    o.anthropic.api_key, o.anthropic.base_url = "test-key", fake["url"]
    o.features.update(features)
    return o


def test_sends_model_effort_adaptive_thinking_and_the_default_refusal_fallback(fake_anthropic):
    audit = InMemoryAuditSink()
    result = ChatFactory(_claude(fake_anthropic), audit).create(opt.SUMMARY).complete("system", [{"role": "user", "content": "Summarize"}])
    body = fake_anthropic["body"]
    assert result.text == "OK" and (result.input_tokens, result.output_tokens) == (10, 2)
    assert (body["model"], body["max_tokens"], body["fallbacks"]) == ("claude-opus-5-5", 16000, "default")
    assert (body["thinking"], body["output_config"], body["stream"]) == ({"type": "adaptive"}, {"effort": "medium"}, True)
    assert "server-side-fallback-2026-07-01" in fake_anthropic["headers"]["anthropic-beta"]
    assert audit.calls[0].succeeded and audit.calls[0].model == "claude-opus-5-5"


def test_fallback_can_be_turned_off_and_effort_follows_configuration(fake_anthropic):
    o = _claude(fake_anthropic, check=opt.FeatureOptions(effort="high"))
    o.anthropic.refusal_fallback = False
    ChatFactory(o, InMemoryAuditSink()).create(opt.CHECK).complete("system", [{"role": "user", "content": "ping"}])
    body = fake_anthropic["body"]
    assert "fallbacks" not in body and body["output_config"] == {"effort": "high"}
    assert "server-side-fallback" not in fake_anthropic["headers"].get("anthropic-beta", "")


def test_tools_are_sent_and_a_tool_call_comes_back_and_goes_back_as_is(fake_anthropic):
    from aqt.ai.chat import Tool
    fake_anthropic["stop"] = "tool_use"
    client = ChatFactory(_claude(fake_anthropic), InMemoryAuditSink()).create(opt.ASK)
    tools = [Tool("list_runs", "Runs", {"type": "object", "properties": {"bank": {"type": "string"}}})]
    messages = [{"role": "user", "content": "Runs of TBC?"}]
    result = client.complete("system", messages, tools=tools)
    assert fake_anthropic["body"]["tools"] == [{"name": "list_runs", "description": "Runs", "input_schema": tools[0].parameters}]
    assert result.finish_reason == "tool_use" and [(c.id, c.name, c.arguments) for c in result.tool_calls] == [("toolu_1", "list_runs", {"bank": "TBCBGE22"})]

    fake_anthropic["stop"] = "end_turn"
    messages += [result.as_message(), {"role": "tool", "tool_call_id": "toolu_1", "name": "list_runs", "content": "3 runs"}]
    client.complete("system", messages, tools=tools)
    sent = fake_anthropic["body"]["messages"]
    assert [b["type"] for b in sent[1]["content"]] == ["text", "tool_use"] and sent[1]["content"][1]["input"] == {"bank": "TBCBGE22"}
    assert sent[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "3 runs"}]}


# ---- audit and masking -------------------------------------------------------------------------------------------

def test_masks_outgoing_text_and_records_the_call():
    from aqt.ai.chat import CallContext
    inner, audit = ScriptedChat(["fine"]), InMemoryAuditSink()
    client = AuditingChat(inner, opt.SUMMARY, audit, MASKER, CallContext("RUN-1", "tester"))
    assert client.complete("sys", [{"role": "user", "content": '{"access_token":"SECRET","iban":"GE29NB0000000101904917"}'}]).text == "fine"
    sent = inner.prompts[0][1][0]["content"]
    assert "SECRET" not in sent and "GE29NB0000000101904917" not in sent
    call = audit.calls[0]
    assert (call.feature, call.run_id, call.triggered_by, call.succeeded, call.input_tokens) == (opt.SUMMARY, "RUN-1", "tester", True, 10)
    assert "SECRET" not in json.dumps(call.request) and len(call.prompt_sha256) == 64


def test_failed_calls_are_recorded_and_raised():
    audit = InMemoryAuditSink()
    client = AuditingChat(ScriptedChat([ProviderError("connection refused")]), opt.ASK, audit, MASKER, None)
    with pytest.raises(ProviderError):
        client.complete("sys", [{"role": "user", "content": "hello"}])
    assert not audit.calls[0].succeeded and "connection refused" in audit.calls[0].error


# ---- summaries -------------------------------------------------------------------------------------------------

SPEC = "NextGenPSD2 IG (ENG) 6.3.1.2 Consent Request on Account List; 14.11.1 (p. 144)"


def _run(run_id, day, *scenarios):
    from datetime import datetime, timezone
    rows = []
    for i, (sid, outcome) in enumerate(scenarios):
        steps = []
        if outcome == "Failed":
            steps = [StepRow(run_id=run_id, ordinal=0, is_cleanup=False, name="POST consent", kind="request", outcome="Failed",
                             failures=["Error code 'FORMAT_ERROR', expected 'PARAMETER_NOT_SUPPORTED'"],
                             exchanges=[ExchangeRow(run_id=run_id, ordinal=0, method="POST", url="https://bank/v2/consents/account-access",
                                                    status_code=400, response_body='{"code":"FORMAT_ERROR"}')])]
        rows.append(ScenarioRow(run_id=run_id, ordinal=i, scenario_id=sid, title=sid, suite="negative", severity="Major", outcome=outcome,
                                spec_ref=SPEC if sid == "NEG-CON-005" else None,
                                reason="Error code 'FORMAT_ERROR', expected 'PARAMETER_NOT_SUPPORTED'" if outcome == "Failed" else None, steps=steps))
    passed = sum(o == "Passed" for _, o in scenarios)
    failed = sum(o == "Failed" for _, o in scenarios)
    return RunRow(id=run_id, source="cli", status="Completed", bank_bic="TESTGE22", created_utc=datetime(2026, 10, day, tzinfo=timezone.utc),
                  passed=passed, failed=failed, pass_rate=round(100 * passed / (passed + failed), 1) if passed + failed else 0, scenarios=rows)


def test_digest_lists_problems_with_evidence_and_computes_changes_in_code():
    previous = _run("PREV", 21, ("PRE-001", "Passed"), ("NEG-CON-005", "Blocked"), ("AIS-001", "Passed"))
    digest = build_digest(_run("CUR", 22, ("PRE-001", "Passed"), ("NEG-CON-005", "Failed"), ("AIS-002", "Skipped")), previous)
    assert digest.problem_scenario_ids == ["NEG-CON-005"]
    assert [(c.scenario_id, c.before, c.after) for c in digest.changes] == [("NEG-CON-005", "Blocked", "Failed")]
    assert "FORMAT_ERROR" in digest.text and "POST https://bank/v2/consents/account-access → 400" in digest.text
    assert "PASSED: PRE-001" in digest.text and SPEC in digest.spec_refs


def test_parses_answers_wrapped_in_think_blocks_and_code_fences():
    s = RunSummary.parse('<think>{not this}</think>\n```json\n{"headline":"h","health":"degraded","findings":[],"attention":[]}\n```')
    assert (s.headline, s.health) == ("h", "degraded")
    with pytest.raises(ValueError):
        RunSummary.parse("Sorry, I cannot help with that.")


def test_validation_drops_unknown_scenarios_canonicalises_clauses_and_flags_unexplained_failures():
    digest = build_digest(_run("CUR", 22, ("NEG-CON-005", "Failed"), ("NEG-CON-006", "Failed")), None)
    s = RunSummary.from_json({"headline": "h", "health": "Degraded", "findings": [
        {"title": "wrong code", "side": "BANK", "severity": "major", "scenarios": ["NEG-CON-005", "NEG-CON-999"], "specRef": "NextGenPSD2 IG 6.3.1.2, 14.11.1"},
        {"title": "made up", "side": "bank", "severity": "Minor", "scenarios": [], "specRef": "PSD2 RTS Article 36"}]})
    notes = s.validate(digest)
    assert s.health == "degraded" and (s.findings[0].side, s.findings[0].severity) == ("bank", "Major")
    assert s.findings[0].scenarios == ["NEG-CON-005"] and s.findings[0].spec_ref == SPEC and s.findings[1].spec_ref == ""
    assert any("NEG-CON-999" in n for n in notes) and any("PSD2 RTS Article 36" in n for n in notes)
    assert any("Not explained by any finding: NEG-CON-006" in n for n in notes)
    assert s.comparison == "No earlier run with results for this bank."


def test_passed_scenarios_become_warnings_skipped_ones_are_dropped_and_comparison_is_code():
    previous = _run("PREV", 21, ("PRE-002", "Passed"), ("PRE-003", "Failed"), ("NEG-CON-005", "Blocked"), ("TBC-1", "Skipped"))
    digest = build_digest(_run("CUR", 22, ("PRE-002", "Passed"), ("PRE-003", "Passed"), ("NEG-CON-005", "Failed"), ("TBC-1", "Skipped")), previous)
    s = RunSummary.from_json({"headline": "h", "health": "degraded", "comparison": "Previously failed NEG-CON-005 is now Failed", "findings": [
        {"title": "certificate not trusted", "scenarios": ["PRE-002"]}, {"title": "TBC features missing", "scenarios": ["TBC-1"]},
        {"title": "wrong code", "scenarios": ["NEG-CON-005"]}]})
    s.validate(digest)
    assert [f.title for f in s.findings] == ["wrong code"]
    assert s.attention == ["PRE-002: certificate not trusted"]
    assert "Blocked → Failed: NEG-CON-005" in s.comparison and "Failed → Passed: PRE-003" in s.comparison


@pytest.mark.parametrize("cited,backed", [("6.3.1.2", True), ("IG 14.11.1", True), ("6.3.1.9", False), ("Berlin Group spec", False)])
def test_clause_numbers_must_all_appear_in_a_run_reference(cited, backed):
    assert (match_spec_ref(cited, [SPEC]) is not None) == backed


# ---- rules (no database) ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("level,quote,expected", [
    ("MUST", "The ASPSP shall reject the request.", None),
    ("MUST", "აუცილებელია ამსმპ-მა დააბრუნოს შეცდომა PERIOD_INVALID.", None),
    ("MAY", "If this function is supported, the ASPSP may offer it.", None),
    ("SHOULD", "2022 წლის 31 მაისამდე მრავლობითი გადახდების მხარდაჭერა უმჯობესია.", None),
    ("MUST", "The call contains the allPsd2 sub attribute.", "no requirement keyword"),
    ("MUST", "The ASPSP may support global consents.", "suggests MAY"),
    ("MUST", "ამ ფუნქციის მხარდაჭერა არასავალდებულოა.", "suggests MAY"),  # "optional" is not "mandatory"
])
def test_questions_a_level_the_quote_does_not_support(level, quote, expected):
    note = question_level(level, quote)
    assert note is None if expected is None else expected in note


def test_duplicates_need_the_same_identifiers_and_ids_use_the_standard_family():
    assert not same_identifiers("accept 'availableAccounts' with value 'allAccounts'.", "accept 'allPsd2' with value 'allAccounts'.")
    assert same_identifiers("rejects validUntil in the past with 400 FORMAT_ERROR.", "A past validUntil is rejected: 400 and FORMAT_ERROR.")
    assert [id_prefix(s) for s in ("BG-IG", "GE-XS2A", "NBG-80-04")] == ["BG", "GE", "NBG"]


# ---- with PostgreSQL ---------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db():
    require_database()
    database = ThrowawayDatabase()
    yield database
    database.drop()


VALID = '{"headline":"Consent validation returns the wrong error code","health":"degraded","findings":[{"title":"Wrong code","side":"bank","severity":"Major","scenarios":["NEG-CON-003"],"explanation":"x","evidence":"Expected status 400 but got 201","specRef":"","nextStep":"Bank fixes validation"}],"attention":[]}'


def _factory(db, answers):
    model = ScriptedChat(answers)
    from aqt.ai.chat import StoreAuditSink
    return ChatFactory(opt.AiOptions(), StoreAuditSink(db.store), db.store.masker,
                       provider_override=lambda settings: model, embed_override=bag_of_words)


def test_summary_is_validated_stored_and_linked_to_its_audit_row(db):
    db.store.save_run(RunInfo(id="SUM-OK", source="cli", status="Completed", bank_bic="TESTGE22"), sample_run())
    result = RunSummarizer(db.store, _factory(db, [VALID])).summarize("SUM-OK", "tester")
    assert result.row.status == "ok" and result.summary.health == "degraded"
    assert db.store.get_latest_run_summary("SUM-OK").id == result.row.id
    with db.store.connect() as conn:
        feature, run_id, request = conn.execute("SELECT feature, run_id, request::text FROM ai_calls WHERE id = %s", (result.row.ai_call_id,)).fetchone()
    assert (feature, run_id) == (opt.SUMMARY, "SUM-OK") and "SECRET" not in request


def test_summary_retries_once_keeps_raw_text_and_records_errors(db):
    db.store.save_run(RunInfo(id="SUM-RETRY", source="cli", status="Completed", bank_bic="TESTGE22"), sample_run())
    assert RunSummarizer(db.store, _factory(db, ["not json", VALID])).summarize("SUM-RETRY").row.status == "ok"
    bad = RunSummarizer(db.store, _factory(db, ["no json", "still no json"])).summarize("SUM-RETRY")
    assert (bad.row.status, bad.row.text) == ("unstructured", "still no json")
    failed = RunSummarizer(db.store, _factory(db, [ProviderError("connection refused")])).summarize("SUM-RETRY")
    assert failed.row.status == "failed" and "connection refused" in failed.row.text


def test_runs_without_results_are_summarized_without_a_model_call(db):
    db.store.save_run(RunInfo(id="SUM-CANCELLED", source="portal", status="Cancelled", bank_bic="TESTGE22", error="Cancelled by user"), None)
    result = RunSummarizer(db.store, _factory(db, [])).summarize("SUM-CANCELLED")
    assert (result.row.status, result.row.provider, result.summary.health) == ("ok", "none", "inconclusive")


CLAUSE_TEXT = ("The TPP sends the validUntil date. If the value exceeds 90 days from the request, the ASPSP shall return the error PERIOD_INVALID.\n"
               "A value of 9999-12-31 shall be replaced by the maximum allowed date.")


def _seed_clause(db, family):
    key = f"{family}-IG:1.0:9.1"
    db.store.replace_standard(StandardDocumentRow(id=f"{family}-IG", version="1.0", title="Test", language="en", url="u", sha256="x"),
                              [ClauseRow(standard=f"{family}-IG", version="1.0", number="9.1", title="Consent term", text=CLAUSE_TEXT,
                                         level=1, ord=0, language="en", key=key)])
    return key


def _answer(*rules):
    return json.dumps({"skipped": "examples", "rules": [
        {"title": "t", "level": level, "service": "AIS", "area": "CON", "requirement": req, "quote": quote, "quoteEnglish": "",
         "operation": "POST /v2/consents/account-access", "notes": "",
         "check": {"kind": "negative", "description": "d", "expectStatus": [400], "expectErrorCodes": ["PERIOD_INVALID"]}}
        for level, req, quote in rules]})


def test_drafts_are_checked_numbered_recorded_and_duplicates_flagged(db):
    key = _seed_clause(db, "TS")
    first = _answer(
        ("MUST", "The ASPSP rejects validUntil beyond 90 days with PERIOD_INVALID.", "If the value exceeds 90 days from the request, the ASPSP shall return the error PERIOD_INVALID."),
        ("MUST", "The ASPSP replaces 9999-12-31 by the maximum date.", "A value of 9999-12-31 must be replaced by the maximum allowed date."),
        ("MAY", "The ASPSP shortens the term.", "A value of 9999-12-31 shall be replaced by the maximum allowed date."))
    # The wrong quote goes back once (repair round); this model answers the same again, so the rule stays invalid.
    result = RuleDrafter(db.store, _factory(db, [first, first])).draft(key, "tester")
    assert [r.id for r in result.rules] == ["TS-CON-001", "TS-CON-002", "TS-CON-003"]
    assert [r.status for r in result.rules] == ["draft", "invalid", "draft"]
    assert any(n.startswith("Quote check failed") for n in result.rules[1].check_notes)
    assert any("suggests MUST" in n for n in result.rules[2].check_notes)
    assert [(h.action, h.reviewer) for h in db.store.get_rule_history("TS-CON-002")] == [("drafted", "tester")]

    again = RuleDrafter(db.store, _factory(db, [_answer(("MUST", "The ASPSP rejects validUntil beyond 90 days with PERIOD_INVALID.",
                                                         "the ASPSP shall return the error PERIOD_INVALID"))])).draft(key)
    assert (again.rules[0].id, again.rules[0].similar_to) == ("TS-CON-004", "TS-CON-001")


def test_a_quote_not_in_the_clause_goes_back_once_and_a_corrected_one_is_accepted(db):
    key = _seed_clause(db, "TF")
    chats = _factory(db, [_answer(("MUST", "Rule E.", "Code 9999-12-31: replaced by the maximum allowed date")),
                          _answer(("MUST", "Rule E.", "A value of 9999-12-31 shall be replaced by the maximum allowed date."))])
    rule = RuleDrafter(db.store, chats).draft(key).rules[0]
    assert rule.status == "draft" and "Quote found in the clause." in rule.check_notes
    with db.store.connect() as conn:
        feedback = conn.execute("SELECT request FROM ai_calls ORDER BY time_utc DESC LIMIT 1").fetchone()[0][-1]["text"]
    assert "could not find these quotes" in feedback and "code 9999-12-31" in feedback.lower()


def test_review_actions_record_the_reviewer_and_guard_the_workflow(db):
    key = _seed_clause(db, "TR")
    rules = RuleDrafter(db.store, _factory(db, [_answer(("MUST", "Rule A about PERIOD_INVALID.", "the ASPSP shall return the error PERIOD_INVALID"),
                                                       ("MUST", "Rule B about the maximum date.", "A value of 9999-12-31 shall be replaced"))])).draft(key).rules
    book, a, b = RuleBook(db.store), rules[0].id, rules[1].id
    with pytest.raises(ReviewError):
        book.approve(a, " ")
    with pytest.raises(ReviewError):
        book.reject(b, "Ana", "")
    with pytest.raises(ReviewError, match="not in the clause"):
        book.edit(a, "Ana", RuleEdit(quote="the ASPSP may ignore it"))

    book.edit(a, "Ana", RuleEdit(level="should", area="err", requirement="Rule A, clarified."))
    approved = book.approve(a, "Ana", "matches the standard")
    assert (approved.status, approved.reviewed_by, approved.level, approved.area) == ("approved", "Ana", "SHOULD", "ERR")
    with pytest.raises(ReviewError):
        book.edit(a, "Ana", RuleEdit(title="x"))  # reopen first
    book.reject(b, "Ana", "duplicates the BG rule")
    book.reopen(b, "Levan")
    assert db.store.get_rule(b).status == "draft"
    history = db.store.get_rule_history(a)
    assert [h.action for h in history] == ["drafted", "edited", "approved"]
    assert (history[1].before["level"], history[1].after["level"]) == ("MUST", "SHOULD")


def test_invalid_draft_becomes_reviewable_when_the_quote_is_fixed(db):
    key = _seed_clause(db, "TQ")
    wrong = _answer(("MUST", "Rule C.", "the bank must return PERIOD_INVALID"))
    rule = RuleDrafter(db.store, _factory(db, [wrong, wrong])).draft(key).rules[0]
    assert rule.status == "invalid"
    fixed = RuleBook(db.store).edit(rule.id, "Ana", RuleEdit(quote="the ASPSP shall return the error PERIOD_INVALID"))
    assert fixed.status == "draft" and "Quote fixed by the reviewer and found in the clause." in fixed.check_notes


def test_a_stale_review_is_refused(db):
    from datetime import datetime, timezone
    from aqt.store.entities import RuleReviewRow
    key = _seed_clause(db, "TP")
    rule = RuleDrafter(db.store, _factory(db, [_answer(("MUST", "Rule D.", "the ASPSP shall return the error PERIOD_INVALID"))])).draft(key).rules[0]
    stale = db.store.get_rule(rule.id)
    RuleBook(db.store).approve(rule.id, "Ana")
    stale.status = "rejected"
    with pytest.raises(ValueError, match="changed by someone else"):
        db.store.update_rule(stale, stale.updated_utc, RuleReviewRow(rule_id=rule.id, time_utc=datetime.now(timezone.utc), reviewer="Levan", action="rejected"))


def test_export_writes_approved_rules_as_yaml_and_removes_stale_files(db, tmp_path):
    import yaml
    key = _seed_clause(db, "TO")
    rule = RuleDrafter(db.store, _factory(db, [_answer(("MUST", 'Rule E with "quotes".', "the ASPSP shall return the error PERIOD_INVALID"))])).draft(key).rules[0]
    RuleBook(db.store).approve(rule.id, "Ana")
    (tmp_path / "_example.yaml").write_text("kept")
    (tmp_path / "to").mkdir()
    (tmp_path / "to" / "TO-OLD-001.yaml").write_text("stale")
    written, removed = RuleBook(db.store).export(str(tmp_path))
    text = (tmp_path / "to" / f"{rule.id}.yaml").read_text()
    data = yaml.safe_load(text)
    assert written >= 1 and removed == 1 and (tmp_path / "_example.yaml").exists()
    assert (data["id"], data["status"], data["requirement"], data["review"]["by"]) == (rule.id, "approved", 'Rule E with "quotes".', "Ana")
    assert to_yaml(db.store.get_rule(rule.id)) == text
