"""The ask-agent (Phase 4): tool-calling message formats, citation check, read-only SQL role, the agent loop."""

import pytest

from aqt.ai.agent import check_citations, cited_ids
from aqt.ai.chat import ChatResult, ToolCall, _anthropic_messages, _ollama_message

from ai_support import ThrowawayDatabase, require_database, sample_run


# ---- without a database ----------------------------------------------------------------------------------------

def test_ids_in_an_answer_are_found_and_checked_against_the_tool_results():
    answer = ("Run 20261021-095156-TBCBGE22-4591 and MOCKGE22-20261024-194234 failed NEG-CON-004 "
              "(rule GE-CON-001, clause GE-XS2A:0.8.RC3:9.1.1.10); NEG-CON-999 too.")
    assert cited_ids(answer) == ["20261021-095156-TBCBGE22-4591", "MOCKGE22-20261024-194234", "GE-XS2A:0.8.RC3:9.1.1.10",
                                 "NEG-CON-004", "GE-CON-001", "NEG-CON-999"]
    evidence = "20261021-095156-tbcbge22-4591 | MOCKGE22-20261024-194234 | NEG-CON-004 GE-CON-001 GE-XS2A:0.8.RC3:9.1.1.10"
    assert check_citations(answer, evidence) == ["NEG-CON-999 is not in the data the agent looked at."]


def test_tool_turns_in_claude_format_keep_its_own_blocks_and_merge_results():
    raw = [{"type": "thinking", "thinking": "…", "signature": "sig"}, {"type": "tool_use", "id": "t1", "name": "list_runs", "input": {}}]
    messages = [{"role": "user", "content": "q"},
                {"role": "assistant", "content": "", "raw": raw, "tool_calls": [{"id": "t1", "name": "list_runs", "arguments": {}},
                                                                               {"id": "t2", "name": "get_run", "arguments": {"run_id": "R"}}]},
                {"role": "tool", "tool_call_id": "t1", "name": "list_runs", "content": "a"},
                {"role": "tool", "tool_call_id": "t2", "name": "get_run", "content": "b"}]
    out = _anthropic_messages(messages)
    assert out[1] == {"role": "assistant", "content": raw}
    assert out[2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "a"},
                                                  {"type": "tool_result", "tool_use_id": "t2", "content": "b"}]}
    assert len(out) == 3


def test_tool_turns_in_ollama_format():
    assert _ollama_message({"role": "assistant", "content": "", "tool_calls": [{"id": "x", "name": "get_run", "arguments": {"run_id": "R"}}]}) == \
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_run", "arguments": {"run_id": "R"}}}]}
    assert _ollama_message({"role": "tool", "tool_call_id": "x", "name": "get_run", "content": "c"}) == \
        {"role": "tool", "content": "c", "tool_name": "get_run"}


# ---- with a database -------------------------------------------------------------------------------------------

class ToolScript:
    """A model that answers from a list of ChatResults and records what it was sent."""
    provider, model = "fake", "scripted"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def complete(self, system, messages, json_schema=None, tools=None):
        self.calls.append((system, [dict(m) for m in messages], tools))
        return self.answers.pop(0)


@pytest.fixture(scope="module")
def db():
    require_database()
    from aqt.store.mapper import RunInfo
    database = ThrowawayDatabase()
    database.store.save_run(RunInfo(id="TESTGE22-20261024-080000", source="cli", status="Completed", bank_bic="TESTGE22"), sample_run())
    yield database
    database.drop()


def test_the_sql_tool_can_only_read_the_agent_views(db):
    from aqt.ai.agent import AgentTools
    tools = AgentTools(db.store)
    assert "NEG-CON-003 | Failed" in tools.call("sql_query", {"sql": "SELECT scenario_id, outcome FROM scenario_results ORDER BY 1"})
    for sql in ("SELECT request_body FROM public.exchanges", "SELECT * FROM public.ai_calls", "DELETE FROM runs",
                "RESET ROLE; SELECT 1", "SELECT set_config('role', current_setting('role'), false) FROM public.runs",
                "CREATE TABLE x (i int)"):
        assert "sql_query failed" in tools.call("sql_query", {"sql": sql}), sql
    # Request/response bodies are not in the views at all.
    assert "SECRET" not in tools.call("sql_query", {"sql": "SELECT * FROM exchanges"})


def test_tool_results_are_masked(db):
    from aqt.ai.agent import AgentTools
    details = AgentTools(db.store).call("get_scenario_details", {"run_id": "TESTGE22-20261024-080000", "scenario_id": "AIS-001"})
    assert "oauth/token -> 200" in details and "SECRET-ACCESS-TOKEN" not in details


def test_the_agent_looks_things_up_answers_and_its_citations_are_checked(db):
    from aqt.ai import options as opt
    from aqt.ai.agent import AskAgent
    from aqt.ai.chat import ChatFactory, InMemoryAuditSink
    model = ToolScript([
        ChatResult("", "scripted", "tool_use", tool_calls=[ToolCall("c1", "list_runs", {"bank": "TESTGE22"})]),
        ChatResult("", "scripted", "tool_use", tool_calls=[ToolCall("c2", "get_run", {"run_id": "TESTGE22-20261024-080000"})]),
        ChatResult("<think>…</think>NEG-CON-003 failed in TESTGE22-20261024-080000 (expected 400, got 201); see also NEG-CON-777.",
                   "scripted", "stop")])
    audit = InMemoryAuditSink()
    factory = ChatFactory(opt.AiOptions(), audit, db.store.masker, provider_override=lambda s: model)
    answer = AskAgent(db.store, factory).ask("Why did TESTGE22 fail? My IBAN is GE29NB0000000101904917", "Ana")

    row = answer.row
    assert answer.text.startswith("NEG-CON-003 failed") and row.status == "unverified"
    assert row.citation_notes == ["NEG-CON-777 is not in the data the agent looked at."]
    assert [t["tool"] for t in row.tool_calls] == ["list_runs", "get_run"]
    assert "GE29NB0000000101904917" not in row.question                    # masked before it is stored ...
    assert "GE29NB0000000101904917" not in model.calls[0][1][0]["content"]  # ... and before it is sent
    assert "NOW:" in model.calls[0][0] and {t.name for t in model.calls[0][2]} >= {"sql_query", "get_run"}
    assert model.calls[2][1][-1]["role"] == "tool" and "FAILED / ERROR SCENARIOS" in model.calls[2][1][-1]["content"]
    assert len(audit.calls) == 3 and "tool calls:" in audit.calls[0].response
    assert db.store.get_agent_question(row.id).answer == answer.text


def test_the_agent_stops_looking_after_the_round_limit(db):
    from aqt.ai import agent, options as opt
    from aqt.ai.chat import ChatFactory, InMemoryAuditSink
    loop = [ChatResult("", "scripted", "tool_use", tool_calls=[ToolCall(f"c{i}", "list_runs", {})]) for i in range(agent.MAX_TOOL_ROUNDS)]
    model = ToolScript(loop + [ChatResult("No idea.", "scripted", "stop")])
    factory = ChatFactory(opt.AiOptions(), InMemoryAuditSink(), db.store.masker, provider_override=lambda s: model)
    row = agent.AskAgent(db.store, factory).ask("Loop forever").row
    assert row.answer == "No idea." and model.calls[-1][2] is None and len(row.tool_calls) == agent.MAX_TOOL_ROUNDS
