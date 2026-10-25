"""
Eval sets for the AI features (plan §7: "each AI feature gets a small eval set before staff use it").

Fixed cases in evals/*.yaml run through the real features against the configured model (Claude or local), each in a
temporary database seeded from the case (the real store is never touched), and are scored by checks in code — no model
judges another model. Every model call is still audited in the real ai_calls table when it is reachable, and kept in
the report. Reports go to reports/evals/<time>-<provider>.json and .md, so providers and prompt changes can be compared.

  summary    run triage on runs whose cause is known: health, findings and their side, nothing reported for passes
  rules      rule drafting on fixed clause texts: number of rules, required topics and levels, quotes verified
  scenarios  scenario drafting from approved rules: reviewable draft, dry-run outcome on MockBank, MockBank-gap judgement
  ask        the ask-agent on a seeded dataset: facts in the answer, citations verified, tools used
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import yaml

from ..results import HttpExchange, Outcome, RunResult, ScenarioResult, StepResult
from ..store.entities import ClauseRow, RuleRow, StandardDocumentRow
from . import options as opt
from .chat import AuditSink, ChatFactory, InMemoryAuditSink, ProviderError, StoreAuditSink

FEATURES = ("summary", "rules", "scenarios", "ask")
FILES = {"summary": "summaries.yaml", "rules": "rules.yaml", "scenarios": "scenarios.yaml", "ask": "ask.yaml"}
MODEL_FEATURE = {"summary": opt.SUMMARY, "rules": opt.RULE_DRAFTING, "scenarios": opt.SCENARIO_DRAFTING, "ask": opt.ASK}


@dataclass
class Check:
    label: str
    ok: bool
    detail: str = ""


@dataclass
class CaseResult:
    feature: str
    name: str
    provider: str
    model: str
    checks: list[Check] = field(default_factory=list)
    duration_ms: int = 0
    error: str | None = None
    output: str = ""

    @property
    def passed(self) -> bool:
        return self.error is None and bool(self.checks) and all(c.ok for c in self.checks)

    @property
    def score(self) -> float:
        return 0.0 if self.error or not self.checks else sum(c.ok for c in self.checks) / len(self.checks)

    def check(self, label: str, ok: bool, detail: str = ""):
        self.checks.append(Check(label, bool(ok), detail))


class TeeAuditSink(AuditSink):
    """Audit records go to every sink (the real store, if reachable, and memory for the report)."""

    def __init__(self, sinks):
        self.sinks = sinks

    def record(self, call):
        for s in self.sinks:
            s.record(call)


# ---- seeding ---------------------------------------------------------------------------------------------------

def _time(value) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def seed_runs(store, runs: list[dict]) -> list[str]:
    """Saves runs described in an eval file (scenarios, steps, one exchange per step). Returns their ids in order."""
    from ..store.mapper import RunInfo
    ids = []
    for r in runs:
        started = _time(r["started"])
        result = RunResult(tool_version="eval", bank_bic=r["bank"], bank_name=r.get("bankName", r["bank"]),
                           xs2a_url=r.get("xs2aUrl", "https://sandbox.example/"), started_at_utc=started,
                           finished_at_utc=started + timedelta(seconds=30))
        for s in r.get("scenarios") or []:
            scenario = ScenarioResult(id=s["id"], title=s.get("title", s["id"]), suite=s.get("suite", "negative"),
                                      severity=s.get("severity", "Major"), spec_ref=s.get("specRef"),
                                      outcome=Outcome(s["outcome"]), reason=s.get("reason"))
            for st in s.get("steps") or []:
                step = StepResult(name=st["name"], kind=st.get("kind", "request"), outcome=Outcome(st.get("outcome", "Passed")),
                                  failures=list(st.get("failures") or []), warnings=list(st.get("warnings") or []))
                if st.get("request"):
                    method, _, url = st["request"].partition(" ")
                    step.exchange = HttpExchange(method=method, url=url, request_headers=[], status_code=st.get("status"),
                                                 response_body=st.get("response"), started_at_utc=started, duration_ms=120)
                scenario.steps.append(step)
            result.scenarios.append(scenario)
        store.save_run(RunInfo(id=r["id"], source="eval", status=r.get("status", "Completed"), bank_bic=r["bank"],
                               bank_name=r.get("bankName"), created_utc=started, started_utc=started,
                               finished_utc=started + timedelta(seconds=30)), result)
        ids.append(r["id"])
    return ids


def clause_row(c: dict) -> ClauseRow:
    number = str(c["number"])
    return ClauseRow(standard=c["standard"], version=str(c["version"]), number=number, title=c.get("title", ""),
                     text=c["text"], level=number.count(".") + 1, ord=0, language=c.get("language", "en"),
                     key=f"{c['standard']}:{c['version']}:{number}")


def seed_clauses(store, clauses: list[dict], factory: ChatFactory | None = None) -> None:
    by_doc: dict[tuple, list[ClauseRow]] = {}
    for c in clauses:
        row = clause_row(c)
        by_doc.setdefault((row.standard, row.version, row.language), []).append(row)
    for (standard, version, language), rows in by_doc.items():
        for i, row in enumerate(rows):
            row.ord = i
        ids = store.replace_standard(StandardDocumentRow(id=standard, version=version, title=standard, language=language,
                                                         url="eval", sha256="eval"), rows)
        if factory is not None:
            try:
                store.set_clause_embeddings(list(zip(ids, factory.embed([r.text for r in rows]))))
            except (ProviderError, TimeoutError, KeyError, OSError):
                pass  # search falls back to words


def rule_row(r: dict, status: str = "approved") -> RuleRow:
    now = datetime.now(timezone.utc)
    number = str(r.get("clauseNumber", "1"))
    return RuleRow(id=r["id"], status=status, title=r.get("title", r["id"]), level=r.get("level", "MUST"),
                   service=r.get("service", "AIS"), area=r.get("area", "GEN"), requirement=r["requirement"],
                   standard=r.get("standard", "EVAL"), version=str(r.get("version", "1")), clause_number=number,
                   clause_key=f"{r.get('standard', 'EVAL')}:{r.get('version', '1')}:{number}", quote=r.get("quote", ""),
                   drafted_by="eval", created_utc=now, updated_utc=now, operation=r.get("operation"),
                   check_json=r.get("check"), reviewed_by="eval fixture" if status == "approved" else None)


# ---- the runner ------------------------------------------------------------------------------------------------

class EvalRunner:
    def __init__(self, server_connection: str, options: opt.AiOptions, root_directory: str, main_store=None, progress=print,
                 provider_override=None, embed_override=None):
        self.server, self.options, self.root, self.progress = server_connection, options, root_directory, progress
        # Tests: a scripted model / embedding function instead of the real providers.
        self._provider_override, self._embed_override = provider_override, embed_override
        self.memory = InMemoryAuditSink()
        sinks = [self.memory] + ([StoreAuditSink(main_store, progress)] if main_store is not None else [])
        self.audit = TeeAuditSink(sinks)

    def load(self, feature: str) -> dict:
        with open(os.path.join(self.root, "evals", FILES[feature]), encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def run(self, features=FEATURES, case_filter: str | None = None) -> list[CaseResult]:
        results = []
        for feature in features:
            spec = self.load(feature)
            cases = [c for c in spec.get("cases") or [] if not case_filter or c["name"] == case_filter]
            if not cases:
                continue
            settings = self.options.for_feature(MODEL_FEATURE[feature])
            self.progress(f"{feature}: {len(cases)} case(s) with {settings.provider} {settings.model}")
            handler = getattr(self, "_" + feature)
            shared = self._ask_dataset(spec) if feature == "ask" else None
            try:
                for case in cases:
                    result = CaseResult(feature, case["name"], settings.provider, settings.model)
                    started = time.monotonic()
                    try:
                        handler(case, result, shared)
                    except Exception as ex:  # noqa: BLE001 - one broken case does not stop the eval
                        result.error = f"{type(ex).__name__}: {ex}"
                    result.duration_ms = int((time.monotonic() - started) * 1000)
                    failed = [c.label for c in result.checks if not c.ok]
                    self.progress(f"  {'PASS' if result.passed else 'FAIL'}  {case['name']:<30} {result.score:.0%}  "
                                  f"{result.duration_ms / 1000:.0f} s" + (f"  — {result.error or '; '.join(failed)}" if not result.passed else ""))
                    results.append(result)
            finally:
                if shared is not None:
                    shared[0].close()
        return results

    def _factory(self, store) -> ChatFactory:
        return ChatFactory(self.options, self.audit, store.masker, provider_override=self._provider_override,
                           embed_override=self._embed_override)

    def _temp(self):
        from ..store import AqtStore
        from ..store.connection import TemporaryDatabase
        db = TemporaryDatabase(self.server, "aqt_eval_")
        return db, AqtStore(db.connection)

    # ---- summary ---------------------------------------------------------------------------------------------

    def _summary(self, case, result: CaseResult, _):
        from .summaries import RunSummarizer
        db, store = self._temp()
        try:
            seed_runs(store, case["runs"])
            outcome = RunSummarizer(store, self._factory(store)).summarize(case["run"], "eval")
        finally:
            db.close()
        expect = case.get("expect") or {}
        summary = outcome.summary
        result.check("readable and valid answer", outcome.row.status == "ok" and summary is not None,
                     f"status {outcome.row.status}; {'; '.join(outcome.row.validation_notes or [])}")
        if summary is None:
            return
        findings = summary.findings
        result.output = summary.headline + "\n" + "\n".join(f"- [{f.side}] {', '.join(f.scenarios)}: {f.title}" for f in findings)
        if "health" in expect:
            result.check(f"health is {' or '.join(expect['health'])}", summary.health in expect["health"], f"got {summary.health}")
        for want in expect.get("findings") or []:
            ids, sides = [s.lower() for s in want["scenariosAny"]], want.get("side") or []
            match = [f for f in findings if any(s.lower() in ids for s in f.scenarios)]
            result.check(f"finding for {'/'.join(want['scenariosAny'])}", bool(match), "no finding names these scenarios")
            if sides and match:
                result.check(f"… on side {' or '.join(sides)}", any(f.side in sides for f in match),
                             "got " + ", ".join(f.side for f in match))
        for sid in expect.get("notFindings") or []:
            wrong = [f.title for f in findings if sid.lower() in (s.lower() for s in f.scenarios)]
            result.check(f"{sid} is not reported as a problem", not wrong, "; ".join(wrong))
        if "maxFindings" in expect:
            result.check(f"at most {expect['maxFindings']} finding(s)", len(findings) <= expect["maxFindings"], f"got {len(findings)}")

    # ---- rules -----------------------------------------------------------------------------------------------

    def _rules(self, case, result: CaseResult, _):
        from .rules import RuleDrafter
        db, store = self._temp()
        try:
            factory = self._factory(store)
            seed_clauses(store, [case["clause"]], factory)
            draft = RuleDrafter(store, factory).draft(clause_row(case["clause"]).key, "eval")
        finally:
            db.close()
        expect = case.get("expect") or {}
        result.check("drafting finished", draft.status == "ok", draft.error or "")
        valid = [r for r in draft.rules if r.status == "draft"]
        invalid = [r for r in draft.rules if r.status == "invalid"]
        result.output = "\n".join(f"- {r.id} [{r.status}] {r.level}: {r.requirement}" for r in draft.rules) + \
            (f"\nskipped: {draft.skipped}" if draft.skipped else "")
        low, high = expect.get("rules", [0, 99])
        result.check(f"{low}–{high} rule(s)", low <= len(valid) <= high, f"got {len(valid)} valid, {len(invalid)} invalid")
        if expect.get("noInvalid"):
            result.check("every quote is in the clause", not invalid,
                         "; ".join(n for r in invalid for n in r.check_notes if n.startswith("Quote check failed"))[:300])
        for group in expect.get("cover") or []:
            alts = [a.lower() for a in group["any"]]
            hits = [r for r in valid if any(a in f"{r.requirement} {r.quote} {r.quote_english or ''} {r.title}".lower() for a in alts)]
            result.check(f"a rule about {'/'.join(group['any'])}", bool(hits))
            if hits and group.get("level"):
                result.check(f"… at level {group['level']}", any(r.level == group["level"] for r in hits),
                             "got " + ", ".join(r.level for r in hits))

    # ---- scenarios -------------------------------------------------------------------------------------------

    def _scenarios(self, case, result: CaseResult, _):
        from ..dryrun import MockDryRun
        from .scenario_drafting import ScenarioDrafter
        db, store = self._temp()
        try:
            store.add_rules([rule_row(case["rule"])], [])
            with MockDryRun(os.path.join(self.root, "banks"), os.path.join(self.root, "scenarios")) as dry_run:
                draft = ScenarioDrafter(store, self._factory(store), os.path.join(self.root, "scenarios"), dry_run).draft(case["rule"]["id"], "eval")
        finally:
            db.close()
        expect = case.get("expect") or {}
        result.output = draft.yaml + "\n" + "\n".join("note: " + n for n in draft.check_notes)
        result.check(f"status {' or '.join(expect.get('status', ['draft']))}", draft.status in expect.get("status", ["draft"]), f"got {draft.status}")
        if "dryRun" in expect:
            result.check(f"dry run {' or '.join(expect['dryRun'])}", draft.dry_run_outcome in expect["dryRun"], f"got {draft.dry_run_outcome}")
        for text in expect.get("yamlContains") or []:
            result.check(f"YAML contains {text}", text.lower() in draft.yaml.lower())
        if "maxAttempts" in expect:
            result.check(f"at most {expect['maxAttempts']} attempt(s)", draft.attempts <= expect["maxAttempts"], f"used {draft.attempts}")
        if "mockGap" in expect:
            result.check("MockBank gap recognised" if expect["mockGap"] else "no MockBank gap claimed",
                         (draft.mock_gap is not None) == expect["mockGap"], draft.mock_gap or "")

    # ---- ask ---------------------------------------------------------------------------------------------------

    def _ask_dataset(self, spec):
        """One temporary database for all questions: runs (with the alerts code computes), clauses, rules."""
        from ..monitor import alerts, config
        db, store = self._temp()
        data = spec.get("dataset") or {}
        for run_id in seed_runs(store, data.get("runs") or []):
            alerts.process_run(store, run_id, config.MonitoringConfig(alerts=config.AlertsConfig(notify=[])), warn=None)
        seed_clauses(store, data.get("clauses") or [], self._factory(store))
        if data.get("rules"):
            store.add_rules([rule_row(r) for r in data["rules"]], [])
        return db, store

    def _ask(self, case, result: CaseResult, shared):
        from .agent import AskAgent
        _, store = shared
        row = AskAgent(store, self._factory(store)).ask(case["question"], "eval").row
        expect = case.get("expect") or {}
        answer = (row.answer or "").lower()
        result.output = (row.answer or row.error or "") + "\n" + "\n".join(f"tool: {t['tool']}({json.dumps(t['arguments'], ensure_ascii=False)})" for t in row.tool_calls)
        result.check("answered", row.answer is not None, row.error or "")
        for group in expect.get("contains") or []:
            result.check(f"mentions {' / '.join(group)}", any(a.lower() in answer for a in group))
        result.check("every cited id was looked up", row.status == "ok", "; ".join(row.citation_notes))
        result.check("used a tool", bool(row.tool_calls))


# ---- report ------------------------------------------------------------------------------------------------------

def write_report(results: list[CaseResult], audit_calls, directory: str) -> tuple[str, str]:
    os.makedirs(directory, exist_ok=True)
    providers = sorted({r.provider for r in results}) or ["none"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(directory, f"{stamp}-{'-'.join(providers)}")
    data = {"createdUtc": datetime.now(timezone.utc).isoformat(), "summary": summarize(results),
            "cases": [{"feature": r.feature, "name": r.name, "provider": r.provider, "model": r.model, "passed": r.passed,
                       "score": round(r.score, 3), "durationMs": r.duration_ms, "error": r.error,
                       "checks": [{"label": c.label, "ok": c.ok, "detail": c.detail} for c in r.checks], "output": r.output}
                      for r in results],
            "aiCalls": [{"id": str(c.id), "feature": c.feature, "model": c.model, "succeeded": c.succeeded,
                         "inputTokens": c.input_tokens, "outputTokens": c.output_tokens, "durationMs": c.duration_ms}
                        for c in audit_calls]}
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    lines = [f"# AI eval — {', '.join(providers)} — {datetime.now():%Y-%m-%d %H:%M}", "",
             "| Feature | Model | Cases passed | Checks passed | Time |", "|---|---|---|---|---|"]
    for s in data["summary"]:
        lines.append(f"| {s['feature']} | {s['model']} | {s['casesPassed']}/{s['cases']} | {s['checksPassed']}/{s['checks']} | {s['seconds']} s |")
    for r in results:
        lines += ["", f"## {r.feature} · {r.name} — {'PASS' if r.passed else 'FAIL'} ({r.score:.0%}, {r.duration_ms / 1000:.0f} s)"]
        if r.error:
            lines.append(f"Error: `{r.error}`")
        lines += [f"- {'✅' if c.ok else '❌'} {c.label}" + (f" — {c.detail}" if c.detail and not c.ok else "") for c in r.checks]
        if r.output:
            lines += ["", "```", r.output.strip()[:2500], "```"]
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return base + ".json", base + ".md"


def summarize(results: list[CaseResult]) -> list[dict]:
    rows = []
    for feature in FEATURES:
        rs = [r for r in results if r.feature == feature]
        if rs:
            rows.append({"feature": feature, "model": f"{rs[0].provider}:{rs[0].model}", "cases": len(rs),
                         "casesPassed": sum(r.passed for r in rs), "checks": sum(len(r.checks) for r in rs),
                         "checksPassed": sum(c.ok for r in rs for c in r.checks), "seconds": sum(r.duration_ms for r in rs) // 1000})
    return rows
