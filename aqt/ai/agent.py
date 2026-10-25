"""
The ask-agent: staff ask questions about runs, results, events, rules and the standards; the model answers by calling
read-only tools over the store, at most MAX_TOOL_ROUNDS rounds.

- Tools are code over the store. The SQL tool logs in as role aqt_agent, which can only read the views in schema
  `agent` (no request/response headers or bodies, no AI audit), one statement, read-only, with a timeout.
- Tool results are masked again before they reach the model (AuditingChat masks every turn as well).
- Facts in code: NOW is given to the model; every run id, scenario/rule id and clause key in the answer is checked against
  the tool results, and ids that never appeared are flagged (status `unverified`). Each question is saved with its tool
  calls in agent_questions; each model call is in ai_calls.
"""

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from ..store.entities import AgentQuestionRow
from . import options as opt
from .chat import CallContext, ChatFactory, ProviderError, Tool
from .rules import _THINK, _prompt
from .summaries import build_digest

MAX_TOOL_ROUNDS = 8
MAX_RESULT_CHARS = 12000
SYSTEM_PROMPT = _prompt("ask_agent.txt")

_VIEWS = """Views (PostgreSQL, schema agent; times are timestamptz in UTC):
- runs(id, source, status, bank_bic, bank_name, created_utc, started_utc, finished_utc, passed, failed, errors, blocked, skipped, pass_rate, suites text[], error)
- scenario_results(id, run_id, bank_bic, run_started_utc, ordinal, scenario_id, title, suite, severity, spec_ref, outcome, reason, duration_ms)
  outcome: Passed | Failed | Error | Blocked | Skipped
- step_results(id, scenario_result_id, run_id, ordinal, is_cleanup, name, kind, outcome, failures text[], warnings text[], duration_ms)
- exchanges(id, step_result_id, run_id, ordinal, method, url, status_code, duration_ms, error, started_utc)   -- no headers or bodies
- events(id, time_utc, run_id, source, type, level, message)
- run_summaries(id, run_id, created_utc, provider, model, status, headline, health)
- rules(id, status, title, level, service, area, requirement, standard, version, clause_number, clause_key, operation, scenarios text[], reviewed_by, reviewed_utc)
- clauses(key, standard, version, number, title, level, language, page)
- alerts(id, created_utc, run_id, previous_run_id, bank_bic, kind, scenario_id, before, after, message, acknowledged_by, acknowledged_utc)
  kind: regression (Passed -> Failed/Error vs the bank's previous run) | fixed | bank_down | run_failed"""

TOOLS = [
    Tool("list_runs", "Test runs, newest first: id, bank, start time, status, counts per outcome, pass rate, suites.",
         {"type": "object", "properties": {
             "bank": {"type": "string", "description": "Bank BIC, e.g. TBCBGE22"},
             "since": {"type": "string", "description": "ISO date or date-time (UTC), inclusive"},
             "until": {"type": "string", "description": "ISO date or date-time (UTC), exclusive"},
             "limit": {"type": "integer", "description": "Default 20, at most 100"}}}),
    Tool("get_run", "One run in detail: totals, changes since the bank's previous run, every failed/error scenario with its "
                    "failing steps and HTTP statuses, blocked/skipped/passed lists, and the AI summary headline if there is one.",
         {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]}),
    Tool("get_scenario_details", "One scenario in one run: every step with failures, warnings and its HTTP requests "
                                 "(method, URL, status, masked response body excerpt).",
         {"type": "object", "properties": {"run_id": {"type": "string"}, "scenario_id": {"type": "string"}},
          "required": ["run_id", "scenario_id"]}),
    Tool("scenario_history", "Outcomes of one scenario across runs (newest first), optionally for one bank.",
         {"type": "object", "properties": {"scenario_id": {"type": "string"}, "bank": {"type": "string"},
                                           "limit": {"type": "integer"}}, "required": ["scenario_id"]}),
    Tool("search_standards", "Search the standards (Berlin Group IG, Georgian guides) by words and meaning; returns clause keys, titles and excerpts.",
         {"type": "object", "properties": {"query": {"type": "string"},
                                           "standard": {"type": "string", "description": "e.g. BG-IG or GE-XS2A"}}, "required": ["query"]}),
    Tool("read_clause", "The text of one clause (by key from search_standards or a rule) and the rules drafted from it.",
         {"type": "object", "properties": {"clause_key": {"type": "string"}}, "required": ["clause_key"]}),
    Tool("find_rules", "Rules from the standards with their status, the scenarios that verify them and the last result per bank. "
                       "Filter by words in the id, title or requirement, and/or by status (draft, approved, rejected).",
         {"type": "object", "properties": {"text": {"type": "string"}, "status": {"type": "string"}}}),
    Tool("sql_query", "One read-only SQL SELECT over the views below (at most 200 rows, 5 s). Use it for counts, grouping "
                      "and time ranges.\n" + _VIEWS,
         {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}),
]

# Ids the answer may cite: run ids (cli: MOCKGE22-20261024-194234, portal: 20261024-194724-MOCKGE22-812d),
# scenario and rule ids (NEG-CON-004, GE-CON-001), clause keys (GE-XS2A:0.8.RC3:9.1.1.10).
_RUN_ID = re.compile(r"\b(?:[A-Z0-9]{4,11}-)?\d{8}-\d{6}(?:-[A-Z0-9]{4,11}-[0-9a-f]{4})?\b")
_CLAUSE_KEY = re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)*:[0-9A-Za-z.]+:\d+(?:\.\d+)*\b")
_ITEM_ID = re.compile(r"\b[A-Z]{2,5}(?:-[A-Z]{2,5}){0,2}-\d{3}\b")


def cited_ids(answer: str) -> list[str]:
    found = []
    for pattern in (_RUN_ID, _CLAUSE_KEY, _ITEM_ID):
        for m in pattern.finditer(answer):
            if not any(m.group() in f for f in found):
                found.append(m.group())
    return found


def check_citations(answer: str, evidence: str) -> list[str]:
    """Ids in the answer that never appeared in a tool result (case-insensitive)."""
    haystack = evidence.lower()
    return [f"{i} is not in the data the agent looked at." for i in cited_ids(answer) if i.lower() not in haystack]


def _parse_time(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _utc(dt) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ") if dt else "-"


class AgentTools:
    """The tools' code: reads the store, returns text for the model (masked, size-capped)."""

    def __init__(self, store, chats: ChatFactory | None = None):
        self._store, self._chats = store, chats

    def call(self, name: str, arguments: dict) -> str:
        handler = getattr(self, "_" + name, None)
        if handler is None or name not in {t.name for t in TOOLS}:
            return f"Unknown tool '{name}'. Available: {', '.join(t.name for t in TOOLS)}."
        try:
            text = handler(**{k: v for k, v in (arguments or {}).items() if v not in (None, "")})
        except TypeError as ex:
            return f"Wrong arguments for {name}: {ex}"
        except ValueError as ex:
            return f"{name}: {ex}"
        except Exception as ex:  # noqa: BLE001 - database errors (e.g. bad SQL) go back to the model so it can fix them
            return f"{name} failed: {type(ex).__name__}: {str(ex).splitlines()[0] if str(ex) else ''}"
        text = self._store.masker.mask_body(text) or ""
        return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + "\n… (cut; ask for less)"

    def _list_runs(self, bank=None, since=None, until=None, limit=20):
        runs = self._store.list_runs(bank, _parse_time(since), _parse_time(until), max(1, min(int(limit), 100)))
        if not runs:
            return "No runs match."
        lines = ["run id | bank | started (UTC) | status | passed/failed/errors/blocked/skipped | pass rate | suites"]
        lines += [f"{r.id} | {r.bank_bic} | {_utc(r.started_utc or r.created_utc)} | {r.status} | "
                  f"{r.passed}/{r.failed}/{r.errors}/{r.blocked}/{r.skipped} | {r.pass_rate:.0f}% | {', '.join(r.suites) or 'all'}" for r in runs]
        return "\n".join(lines)

    def _get_run(self, run_id):
        run = self._store.get_run(run_id)
        if run is None:
            return f"No run '{run_id}'. Use list_runs for ids."
        text = build_digest(run, self._store.get_previous_run(run)).text
        started = run.started_utc or run.created_utc
        later = [r for r in self._store.list_runs(run.bank_bic, since=started, limit=100) if r.id != run.id][::-1]
        text += "\nLATER RUNS OF THIS BANK: " + ("none" if not later else "; ".join(
            f"{r.id} ({_utc(r.started_utc or r.created_utc)}, {r.status}, pass rate {r.pass_rate:.0f}%, failed {r.failed})" for r in later[:10])) + "\n"
        summary = self._store.get_latest_run_summary(run.id)
        if summary is not None and summary.summary_json:
            text += f"\nAI SUMMARY ({summary.provider}:{summary.model}): {summary.summary_json.get('headline', '')}\n"
        return text

    def _get_scenario_details(self, run_id, scenario_id):
        run = self._store.get_run(run_id)
        if run is None:
            return f"No run '{run_id}'."
        scenario = next((s for s in run.scenarios if s.scenario_id.lower() == scenario_id.lower()), None)
        if scenario is None:
            return f"Run {run.id} has no scenario '{scenario_id}'. It ran: {', '.join(s.scenario_id for s in run.scenarios)}"
        lines = [f"{scenario.scenario_id} in run {run.id} (bank {run.bank_bic}): {scenario.outcome}" + (f" — {scenario.reason}" if scenario.reason else ""),
                 f"Title: {scenario.title} · severity {scenario.severity} · spec: {scenario.spec_ref or '-'}"]
        for step in scenario.steps:
            lines.append(f"- {'cleanup ' if step.is_cleanup else ''}step {step.ordinal + 1} '{step.name}' ({step.kind}): {step.outcome}")
            lines += [f"    failure: {f}" for f in step.failures]
            lines += [f"    warning: {w}" for w in step.warnings]
            for x in step.exchanges:
                lines.append(f"    {x.method} {x.url} -> {x.status_code if x.status_code is not None else 'no response'}"
                             + (f" ({x.error})" if x.error else "") + f" in {x.duration_ms} ms")
                if x.response_body and (step.outcome != "Passed" or x.status_code and x.status_code >= 400):
                    lines.append("      response: " + x.response_body[:800].replace("\n", " "))
        return "\n".join(lines)

    def _scenario_history(self, scenario_id, bank=None, limit=20):
        rows = self._store.scenario_history(scenario_id, bank, max(1, min(int(limit), 100)))
        if not rows:
            return f"No results for scenario '{scenario_id}'" + (f" on {bank}" if bank else "") + "."
        return "\n".join(["run id | bank | started (UTC) | outcome | reason"] +
                         [f"{r[0]} | {r[1]} | {_utc(r[2])} | {r[3]} | {r[4] or ''}" for r in rows])

    def _search_standards(self, query, standard=None):
        vector = None
        if self._chats is not None:
            try:
                vector = self._chats.embed([query])[0]
            except (ProviderError, TimeoutError, KeyError):
                vector = None  # search by words only
        hits = self._store.search_clauses(query, vector, standard, 8)
        if not hits:
            return "No clauses found."
        return "\n".join(f"{h.clause.key} · {h.clause.title} · {' '.join(h.clause.text.split())[:300]}" for h in hits)

    def _read_clause(self, clause_key):
        clause = self._store.get_clause(clause_key)
        if clause is None:
            return f"No clause '{clause_key}'. Use search_standards for keys."
        rules = self._store.list_rules(clause_key=clause.key)
        text = clause.text if len(clause.text) <= 5000 else clause.text[:5000] + " …"
        lines = [f"{clause.key} · {clause.title} (language {clause.language}{f', page {clause.page}' if clause.page else ''})", text]
        lines += ["RULES FROM THIS CLAUSE:" if rules else "No rules drafted from this clause."]
        lines += [f"- {r.id} [{r.status}] {r.level}: {r.requirement}" for r in rules]
        return "\n".join(lines)

    def _find_rules(self, text=None, status=None):
        words = [w for w in re.findall(r"\w+", (text or "").lower()) if len(w) > 2]
        rules = [r for r in self._store.list_rules(status)
                 if not words or any(w in f"{r.id} {r.title} {r.requirement}".lower() for w in words)][:30]
        if not rules:
            return "No rules match."
        outcomes = self._store.latest_outcomes(sorted({s for r in rules for s in r.scenarios}))
        lines = []
        for r in rules:
            lines.append(f"{r.id} [{r.status}] {r.level} {r.service} · {r.clause_key} · {r.title}: {r.requirement}")
            for sid in r.scenarios:
                results = [f"{bank} {o[0]} ({o[1]})" for (s, bank), o in outcomes.items() if s == sid]
                lines.append(f"    scenario {sid}: {', '.join(results) or 'never run'}")
        return "\n".join(lines)

    def _sql_query(self, sql):
        columns, rows, truncated = self._store.agent_query(sql)
        if not columns:
            return "The statement returned no rows."
        out = [" | ".join(columns)] + [" | ".join("" if v is None else _utc(v) if isinstance(v, datetime) else str(v) for v in row) for row in rows]
        if truncated:
            out.append("… (more than 200 rows; aggregate or add LIMIT)")
        return "\n".join(out) if rows else " | ".join(columns) + "\n(no rows)"


@dataclass
class Answer:
    row: AgentQuestionRow

    @property
    def text(self) -> str:
        return self.row.answer or ""


class AskAgent:
    def __init__(self, store, chats: ChatFactory):
        self._store, self._chats = store, chats
        self._tools = AgentTools(store, chats)

    def ask(self, question: str, asked_by: str | None = None, on_tool=None) -> Answer:
        """on_tool(name, arguments): called before each tool runs (progress for the CLI)."""
        question = (question or "").strip()
        if not question:
            raise ValueError("Ask a question.")
        started = time.monotonic()
        settings = self._chats.describe(opt.ASK)
        now = datetime.now().astimezone()
        system = (SYSTEM_PROMPT + f"\nNOW: {_utc(now)} (local time {now:%Y-%m-%d %H:%M %z})\n")
        messages: list[dict] = [{"role": "user", "content": question}]
        trace, evidence, call_ids = [], [], []
        answer, error = None, None

        for round_ in range(MAX_TOOL_ROUNDS + 1):
            call_id = uuid.uuid4()
            call_ids.append(call_id)
            client = self._chats.create(opt.ASK, CallContext(None, asked_by, call_id))
            last_round = round_ == MAX_TOOL_ROUNDS
            if last_round:
                messages.append({"role": "user", "content": "That is enough looking up; answer now with what you have found."})
            try:
                result = client.complete(system, messages, tools=None if last_round else TOOLS)
            except (ProviderError, TimeoutError) as ex:
                error = f"{type(ex).__name__}: {ex}"
                break
            if result.finish_reason == "refusal":
                error = "The model declined to answer (refusal)."
                break
            if not result.tool_calls:
                answer = _THINK.sub("", result.text).strip()
                break
            messages.append(result.as_message())
            for call in result.tool_calls:
                if on_tool:
                    on_tool(call.name, call.arguments)
                output = self._tools.call(call.name, call.arguments)
                evidence.append(output)
                trace.append({"tool": call.name, "arguments": call.arguments, "result": output[:2000]})
                messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": output})

        notes = check_citations(answer, "\n".join(evidence)) if answer else []
        status = "failed" if answer is None else "unverified" if notes else "ok"
        if answer is not None and not trace:
            notes.append("Answered without looking anything up.")
        masker = self._store.masker
        row = AgentQuestionRow(
            id=uuid.uuid4(), asked_utc=datetime.now(timezone.utc), question=masker.mask_body(question) or "", status=status,
            provider=settings.provider, model=settings.model, asked_by=asked_by, answer=masker.mask_body(answer) if answer else None,
            tool_calls=json.loads(json.dumps(trace, default=str)), citation_notes=notes, ai_call_ids=call_ids,
            error=error or (None if answer is not None else "No answer."), duration_ms=int((time.monotonic() - started) * 1000))
        try:
            self._store.add_agent_question(row)
        except Exception as ex:  # noqa: BLE001 - the answer is still shown; the history row is lost
            row.citation_notes.append(f"Not saved to the question history: {type(ex).__name__}: {ex}")
        return Answer(row)
