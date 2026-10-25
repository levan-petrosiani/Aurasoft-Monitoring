"""
AI run summary (triage): a stored run -> digest (facts computed in code) -> model -> validated in code -> run_summaries.

The model groups problems by cause and explains them; counts, run-to-run changes, scenario ids and clause references
are computed or checked in code, so a summary can never cite a scenario or clause the run does not contain.
"""

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib import resources

from ..results import format_rate
from ..store.entities import RunRow, RunSummaryRow
from . import options as opt
from .chat import CallContext, ChatFactory, ProviderError

HEALTH_VALUES = ("healthy", "degraded", "broken", "inconclusive")
SIDE_VALUES = ("bank", "our-side", "environment", "unclear")
SEVERITY_VALUES = ("Critical", "Major", "Minor", "Info")

_MAX_DETAILED_SCENARIOS = 30
_MAX_BODY_CHARS = 1500
_MAX_PASSED_WARNINGS = 15
_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
# Section numbers with at least one dot: 6.3.1.2, 14.11.1 (not page numbers or versions like v2).
_CLAUSE_NUMBER = re.compile(r"\b\d+(?:\.\d+)+\b")


def _prompt(name: str) -> str:
    return resources.files("aqt.ai").joinpath("prompts", name).read_text(encoding="utf-8")


SUMMARY_SCHEMA = json.loads(_prompt("run_summary.schema.json"))
SYSTEM_PROMPT = _prompt("run_summary.txt") + _prompt("run_summary.schema.json")


# ---- digest ----------------------------------------------------------------------------------------------------

@dataclass
class OutcomeChange:
    scenario_id: str
    before: str | None
    after: str | None


@dataclass
class RunDigest:
    """The run as the model sees it, plus the facts the answer is checked against."""
    text: str
    scenario_ids: set[str]
    problem_scenario_ids: list[str]
    spec_refs: set[str]
    changes: list[OutcomeChange]
    outcomes: dict[str, str]
    comparison: str               # "since the previous run", written in code
    previous_run_id: str | None = None


def compare(run: RunRow, previous: RunRow | None) -> list[OutcomeChange]:
    if previous is None:
        return []
    before = {}
    for s in previous.scenarios:
        before.setdefault(s.scenario_id.lower(), (s.scenario_id, s.outcome))
    after = {}
    for s in run.scenarios:
        after.setdefault(s.scenario_id.lower(), (s.scenario_id, s.outcome))
    changes = []
    for key in before.keys() | after.keys():
        b, a = before.get(key), after.get(key)
        # Only scenarios run both times count as changes; a different selection is not a regression.
        if b and a and b[1] != a[1]:
            changes.append(OutcomeChange(a[0], b[1], a[1]))
    return sorted(changes, key=lambda c: c.scenario_id)


def describe_changes(run: RunRow, previous: RunRow | None, changes: list[OutcomeChange]) -> str:
    if previous is None:
        return "No earlier run with results for this bank."
    rate = f"Pass rate {format_rate(previous.pass_rate)}% → {format_rate(run.pass_rate)}% (previous run {previous.id})."
    if not changes:
        return rate + " Every scenario run both times has the same outcome."
    groups: dict[str, list[str]] = {}
    for c in changes:
        groups.setdefault(f"{c.before} → {c.after}", []).append(c.scenario_id)
    return rate + " " + ". ".join(f"{k}: {', '.join(v)}" for k, v in sorted(groups.items())) + "."


def _excerpt(body: str) -> str:
    one_line = " ".join(body.splitlines())
    return one_line if len(one_line) <= _MAX_BODY_CHARS else one_line[:_MAX_BODY_CHARS] + "…"


def _append_scenario(out: list[str], s):
    out.append(f"### {s.scenario_id} · suite {s.suite} · severity {s.severity} · {s.outcome}")
    out.append(f"Title: {s.title}")
    if s.spec_ref is not None:
        out.append(f"Spec: {s.spec_ref}")
    if s.reason is not None:
        out.append(f"Reason: {s.reason}")
    for step in s.steps:
        if step.outcome == "Passed" and not step.warnings:
            continue
        out.append(f'Step "{step.name}" ({step.kind}{", cleanup" if step.is_cleanup else ""}) {step.outcome}'
                   + (" — our own QWAC/QSealC, checked locally; no request was sent to the bank" if step.kind == "localCertificates" else ""))
        out.extend("  failure: " + f for f in step.failures)
        out.extend("  warning: " + w for w in step.warnings)
        for x in step.exchanges:
            status = str(x.status_code) if x.status_code is not None else "no response"
            out.append(f"  {x.method} {x.url} → {status} ({x.duration_ms} ms){'' if x.error is None else ' error: ' + x.error}")
            if step.outcome != "Passed" and (x.response_body or "").strip():
                out.append("  response body: " + _excerpt(x.response_body))


def build_digest(run: RunRow, previous: RunRow | None) -> RunDigest:
    out: list[str] = []
    duration = ""
    if run.started_utc and run.finished_utc:
        duration = f" · duration {round((run.finished_utc - run.started_utc).total_seconds())} s"
    started = run.started_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ") if run.started_utc else ""
    out.append(f"RUN {run.id} · bank {run.bank_bic} ({run.bank_name}) · XS2A {run.xs2a_url} · started from {run.source} · status {run.status}")
    out.append(f"Started {started}{duration} · suites: {'all' if not run.suites else ', '.join(run.suites)}")
    out.append(f"Totals: passed {run.passed}, failed {run.failed}, errors {run.errors}, blocked {run.blocked}, skipped {run.skipped} · pass rate {format_rate(run.pass_rate)}%")
    if run.error is not None:
        out.append(f"Run-level error: {run.error}")

    changes = compare(run, previous)
    out.append("")
    if previous is None:
        out.append("CHANGES SINCE PREVIOUS RUN: there is no earlier run with results for this bank.")
    else:
        created = previous.created_utc.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
        out.append(f"CHANGES SINCE PREVIOUS RUN ({previous.id}, {created}Z, pass rate {format_rate(previous.pass_rate)}%):")
        if not changes:
            out.append("- none: every scenario run in both has the same outcome")
        out.extend(f"- {c.scenario_id}: {c.before or 'not run'} → {c.after or 'not run'}" for c in changes)

    problems = [s for s in run.scenarios if s.outcome in ("Failed", "Error")]
    blocked = [s for s in run.scenarios if s.outcome == "Blocked"]
    out.append("")
    out.append("FAILED / ERROR SCENARIOS:" if problems else "FAILED / ERROR SCENARIOS: none")
    for index, s in enumerate(problems):
        if index >= _MAX_DETAILED_SCENARIOS:
            out.append(f"### {s.scenario_id} · {s.outcome} · {s.reason} (details omitted: too many problems)")
            continue
        _append_scenario(out, s)

    if blocked:
        out.append("")
        out.append("BLOCKED (not executed because a critical preflight scenario failed):")
        out.extend(f"- {b.scenario_id}: {b.reason}" for b in blocked)

    skipped = [s for s in run.scenarios if s.outcome == "Skipped"]
    if skipped:
        out.append("")
        out.append("SKIPPED (the bank profile does not declare the feature; not a problem):")
        groups: dict[str, list[str]] = {}
        for s in skipped:
            groups.setdefault(s.reason or "", []).append(s.scenario_id)
        out.extend(f"- {', '.join(ids)}: {reason}" for reason, ids in groups.items())

    passed = [s for s in run.scenarios if s.outcome == "Passed"]
    out.append("")
    out.append(f"PASSED: {', '.join(s.scenario_id for s in passed) if passed else 'none'}")
    warnings: list[str] = []
    for s in passed:
        for w in dict.fromkeys(w for st in s.steps for w in st.warnings):
            line = f"{s.scenario_id}: {w}"
            if line not in warnings and len(warnings) < _MAX_PASSED_WARNINGS:
                warnings.append(line)
    if warnings:
        out.append("WARNINGS IN PASSED SCENARIOS (deviations that did not fail the test):")
        out.extend("- " + w for w in warnings)

    outcomes = {}
    for s in run.scenarios:
        outcomes.setdefault(s.scenario_id.lower(), s.outcome)
    return RunDigest(
        text="\n".join(out) + "\n",
        scenario_ids={s.scenario_id.lower() for s in run.scenarios},
        problem_scenario_ids=[s.scenario_id for s in problems],
        spec_refs={s.spec_ref for s in run.scenarios if s.spec_ref},
        changes=changes, outcomes=outcomes, comparison=describe_changes(run, previous, changes),
        previous_run_id=previous.id if previous else None)


# ---- the summary -----------------------------------------------------------------------------------------------

@dataclass
class Finding:
    title: str = ""
    side: str = "unclear"
    severity: str = "Major"
    scenarios: list[str] = field(default_factory=list)
    explanation: str = ""
    evidence: str = ""
    spec_ref: str = ""
    next_step: str = ""


@dataclass
class RunSummary:
    headline: str = ""
    health: str = "inconclusive"
    findings: list[Finding] = field(default_factory=list)
    comparison: str = ""          # set in code by validate()
    attention: list[str] = field(default_factory=list)

    @staticmethod
    def from_json(d: dict) -> "RunSummary":
        def g(obj, *names, default=""):
            lower = {k.lower(): v for k, v in obj.items()}
            for n in names:
                if n.lower() in lower and lower[n.lower()] is not None:
                    return lower[n.lower()]
            return default
        findings = [Finding(str(g(f, "title")), str(g(f, "side", default="unclear")), str(g(f, "severity", default="Major")),
                            [str(x) for x in g(f, "scenarios", default=[]) or []], str(g(f, "explanation")),
                            str(g(f, "evidence")), str(g(f, "specRef", "spec_ref")), str(g(f, "nextStep", "next_step")))
                    for f in g(d, "findings", default=[]) or [] if isinstance(f, dict)]
        return RunSummary(str(g(d, "headline")), str(g(d, "health", default="inconclusive")), findings,
                          str(g(d, "comparison")), [str(a) for a in g(d, "attention", default=[]) or []])

    def to_json(self) -> dict:
        """camelCase, the same shape the C# tool stored (so old and new summaries render alike)."""
        return {"headline": self.headline, "health": self.health, "comparison": self.comparison, "attention": self.attention,
                "findings": [{"title": f.title, "side": f.side, "severity": f.severity, "scenarios": f.scenarios,
                              "explanation": f.explanation, "evidence": f.evidence, "specRef": f.spec_ref,
                              "nextStep": f.next_step} for f in self.findings]}

    @staticmethod
    def parse(text: str) -> "RunSummary":
        """Reads the model's answer: tolerates code fences, a reasoning preamble or <think> blocks around the JSON."""
        cleaned = _THINK.sub("", text)
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("The answer contains no JSON object")
        data = json.loads(cleaned[start:end + 1])
        if not isinstance(data, dict):
            raise ValueError("The JSON is not an object")
        return RunSummary.from_json(data)

    def validate(self, digest: RunDigest) -> list[str]:
        """Checks the summary against the run in code and fixes what can be fixed. Returns what changed or is missing."""
        notes: list[str] = []
        self.health = _normalize(self.health, HEALTH_VALUES, "inconclusive", "health", notes)
        if not self.headline.strip():
            notes.append("The summary has no headline.")

        for f in self.findings:
            f.side = _normalize(f.side, SIDE_VALUES, "unclear", f'side of "{f.title}"', notes)
            f.severity = _normalize(f.severity, SEVERITY_VALUES, "Major", f'severity of "{f.title}"', notes)
            unknown = [s for s in f.scenarios if s.lower() not in digest.scenario_ids]
            notes.extend(f'Removed scenario "{s}" from "{f.title}": it is not in this run.' for s in unknown)
            f.scenarios = list(dict.fromkeys(s for s in f.scenarios if s not in unknown))
            if f.spec_ref:
                canonical = match_spec_ref(f.spec_ref, digest.spec_refs)
                if canonical is not None:
                    f.spec_ref = canonical  # models shorten references; store the run's exact wording
                else:
                    notes.append(f'Cleared clause "{f.spec_ref}" in "{f.title}": no scenario in this run references it.')
                    f.spec_ref = ""

        # Findings are for problems. One that only names skipped scenarios is dropped (a skip is expected, never a
        # problem); one that names passed (and skipped) scenarios is a warning: moved to attention.
        for f in [f for f in self.findings if f.scenarios and all(digest.outcomes.get(s.lower()) in ("Passed", "Skipped") for s in f.scenarios)]:
            self.findings.remove(f)
            if all(digest.outcomes.get(s.lower()) == "Skipped" for s in f.scenarios):
                notes.append(f'Removed "{f.title}": it is only about skipped scenarios, which are expected.')
                continue
            passed = [s for s in f.scenarios if digest.outcomes.get(s.lower()) == "Passed"]
            self.attention.append(f"{', '.join(passed)}: {f.title}")
            notes.append(f'Moved "{f.title}" to warnings: its scenarios passed.')

        self.comparison = digest.comparison

        explained = {s.lower() for f in self.findings for s in f.scenarios}
        unexplained = [s for s in digest.problem_scenario_ids if s.lower() not in explained]
        if unexplained:
            notes.append(f"Not explained by any finding: {', '.join(unexplained)}.")
        if not digest.problem_scenario_ids and self.health == "broken":
            notes.append('Health "broken" although no scenario failed or errored.')
        return notes

    def to_text(self) -> str:
        lines = [f"[{self.health.upper()}] {self.headline}"]
        for i, f in enumerate(self.findings, 1):
            lines.append("")
            lines.append(f"{i}. {f.title}  ({f.severity}, {f.side}){' · ' + f.spec_ref if f.spec_ref else ''}")
            if f.scenarios:
                lines.append(f"   scenarios: {', '.join(f.scenarios)}")
            lines.append(f"   {f.explanation}")
            if f.evidence:
                lines.append(f"   evidence: {f.evidence}")
            if f.next_step:
                lines.append(f"   next: {f.next_step}")
        if self.comparison:
            lines += ["", "Since the previous run: " + self.comparison]
        if self.attention:
            lines += ["", "Worth a look:"] + [f"  - {a}" for a in self.attention]
        return "\n".join(lines)


def match_spec_ref(cited: str, run_refs) -> str | None:
    """The run's reference the cited one refers to: same text, or every clause number cited (6.3.1.2, 14.11.1) appears
    in it. None when the citation is not backed by the run."""
    refs = list(run_refs)
    for r in refs:
        if cited.lower() in r.lower() or r.lower() in cited.lower():
            return r
    numbers = _CLAUSE_NUMBER.findall(cited)
    if not numbers:
        return None
    for r in refs:
        in_ref = set(_CLAUSE_NUMBER.findall(r))
        if all(n in in_ref for n in numbers):
            return r
    return None


def _normalize(value: str, allowed, fallback: str, what: str, notes: list[str]) -> str:
    match = next((a for a in allowed if a.lower() == (value or "").strip().lower()), None)
    if match is not None:
        return match
    notes.append(f'Unknown {what} "{value}" replaced by "{fallback}".')
    return fallback


# ---- the summarizer ----------------------------------------------------------------------------------------------

@dataclass
class SummaryResult:
    row: RunSummaryRow
    summary: RunSummary | None


class RunSummarizer:
    """Turns a stored run into a triage, checks the answer in code and stores it in run_summaries.
    Never raises for model problems: they are stored as status failed."""

    def __init__(self, store, chats: ChatFactory):
        self._store, self._chats = store, chats

    def summarize(self, run_id: str, triggered_by: str | None = None) -> SummaryResult:
        run = self._store.get_run(run_id)
        if run is None:
            raise ValueError(f"Run '{run_id}' is not in the store")
        settings = self._chats.describe(opt.SUMMARY)
        started = time.monotonic()
        row = RunSummaryRow(id=uuid.uuid4(), run_id=run_id, created_utc=datetime.now(timezone.utc),
                            provider=settings.provider, model=settings.model, status="failed", triggered_by=triggered_by)

        if not run.scenarios:
            # Nothing for a model to explain; say so without spending a call.
            summary = RunSummary(headline=f"No results: the run ended as {run.status}{'' if run.error is None else f' ({run.error})'} before any scenario finished.",
                                 health="inconclusive", comparison="Not compared: this run has no results.")
            row.provider = row.model = "none"
            row.status, row.summary_json = "ok", summary.to_json()
        else:
            digest = build_digest(run, self._store.get_previous_run(run))
            summary = self._ask(digest, run.id, triggered_by, row)

        row.duration_ms = int((time.monotonic() - started) * 1000)
        self._store.add_run_summary(row)
        return SummaryResult(row, summary)

    def _ask(self, digest: RunDigest, run_id: str, triggered_by: str | None, row: RunSummaryRow) -> RunSummary | None:
        messages = [{"role": "user", "content": digest.text}]
        # Two attempts: the second one shows the model why its first answer could not be read.
        for _ in range(2):
            call_id = uuid.uuid4()
            client = self._chats.create(opt.SUMMARY, CallContext(run_id, triggered_by, call_id))
            schema = SUMMARY_SCHEMA if row.provider == opt.OLLAMA else None
            row.ai_call_id = call_id
            try:
                result = client.complete(SYSTEM_PROMPT, messages, schema)
            except ProviderError as ex:
                row.text = f"{ex}" if "not reachable" in str(ex) else f"ProviderError: {ex}"
                return None
            except TimeoutError as ex:
                row.text = f"The model did not answer in time ({ex}). A local model may still be loading; try again or raise the timeout."
                return None

            row.model = result.model or row.model
            if result.finish_reason == "refusal":
                row.text = "The model declined to answer (refusal). Try again, or switch the summary feature to the local model."
                return None
            try:
                summary = RunSummary.parse(result.text)
            except ValueError as ex:
                row.status, row.text = "unstructured", result.text
                messages += [{"role": "assistant", "content": result.text},
                             {"role": "user", "content": f"Your answer could not be read as the required JSON ({ex}). Reply again with only the JSON object."}]
                continue
            row.validation_notes = summary.validate(digest)
            row.summary_json, row.status, row.text = summary.to_json(), "ok", None
            return summary
        return None
