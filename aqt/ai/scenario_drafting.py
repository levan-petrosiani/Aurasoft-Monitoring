"""
Test scenarios from approved rules: AI drafts the YAML; code owns the fields that link it to the standard, checks it
with the engine's own loader and runs it on an in-process MockBank (never a real bank); failures go back to the model,
at most MAX_ATTEMPTS times. Only a person (ScenarioReview) turns a draft into a file in scenarios/.

Facts in code: id, suite, severity, specRef, origin and rules are set by code, not taken from the model; template
names, safe targets and the rule's expected status/error codes are checked in code; the dry-run outcome is the
engine's. The model only judges whether a failed dry run is the scenario's fault or a gap in MockBank, and a
reviewer decides on that.
"""

import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import yaml

from .. import scenarios as engine, yamljson
from ..errors import ConfigurationError
from ..results import Outcome, ScenarioResult
from ..store.entities import RuleRow, ScenarioDraftRow
from . import options as opt
from .chat import CallContext, ChatFactory, ProviderError
from .rules import _THINK, _prompt

MAX_ATTEMPTS = 3

SYSTEM_PROMPT = _prompt("scenario_drafting.txt")
EXAMPLES = ("negative/NEG-CON-004-frequency-per-day.yaml", "ais/AIS-001-account-list-consent.yaml")
# For payment rules the flow example is a payment instead of a consent.
PIS_EXAMPLES = ("negative/NEG-PIS-001-forbidden-field.yaml", "pis/PIS-001-domestic-payment.yaml")

_PAYMENT_PATH = re.compile(r"(^|/)(v\d+/)?((bulk-|periodic-)?payments|signing-baskets)(/|$)", re.I)
_FENCE = re.compile(r"```(?:ya?ml)?\s*\n(.*?)```", re.S | re.I)
_GAP = re.compile(r"^\s*MOCK_GAP:\s*(.+)$", re.M)
_SEVERITY = {"MUST": "Major", "SHOULD": "Minor", "MAY": "Info"}
# Order of keys in the written file; the model's other keys follow.
_KEY_ORDER = ("id", "title", "suite", "severity", "specRef", "origin", "rules", "description", "requires", "steps", "cleanup")


# ---- ids and code-owned fields ---------------------------------------------------------------------------------

def suite_and_prefix(rule: RuleRow) -> tuple[str, str]:
    """negative rules -> negative / NEG-<area>; otherwise by service: ais / AIS, pis / PIS, common / COM."""
    if (rule.check_json or {}).get("kind") == "negative":
        return "negative", f"NEG-{rule.area}"
    return {"AIS": ("ais", "AIS"), "PIS": ("pis", "PIS")}.get(rule.service, ("common", "COM"))


def next_scenario_id(prefix: str, taken: set[str]) -> str:
    pattern = re.compile(rf"^{re.escape(prefix.lower())}-(\d{{3}})$")
    numbers = [int(m.group(1)) for t in taken if (m := pattern.match(t.lower()))]
    return f"{prefix}-{max(numbers, default=0) + 1:03d}"


def slug(title: str) -> str:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return "-".join(words[:8]) or "scenario"


@dataclass
class Identity:
    """What code decides about a draft: never taken from the model."""
    id: str
    suite: str
    severity: str
    spec_ref: str
    origin: str
    rule_id: str


def apply_identity(tree: dict, identity: Identity) -> dict:
    tree = {k: v for k, v in tree.items() if k not in ("id", "suite", "severity", "specRef", "origin", "rules")}
    tree.update({"id": identity.id, "suite": identity.suite, "severity": identity.severity, "specRef": identity.spec_ref,
                 "origin": identity.origin, "rules": [identity.rule_id]})
    ordered = {k: tree[k] for k in _KEY_ORDER if k in tree}
    ordered.update({k: v for k, v in tree.items() if k not in ordered})
    return ordered


def to_yaml(tree: dict, rule: RuleRow) -> str:
    header = (f"# Drafted by AI from rule {rule.id} ({rule.level}): {rule.requirement}\n"
              f"# Checked by aqt (engine loader, dry run on MockBank) and approved by a person before it was added to scenarios/.\n")
    body = yaml.safe_dump(tree, sort_keys=False, allow_unicode=True, width=1000, default_flow_style=None)
    return header + body


# ---- checks in code --------------------------------------------------------------------------------------------

def extract(text: str) -> tuple[str, str | None]:
    """The YAML of the answer and the model's MOCK_GAP explanation, if any."""
    cleaned = _THINK.sub("", text)
    gap = _GAP.search(cleaned)
    match = _FENCE.search(cleaned)
    body = match.group(1) if match else cleaned.replace(gap.group(0), "") if gap else cleaned
    return body.strip() + "\n", gap.group(1).strip() if gap else None


def check_scenario(text: str, source: str, templates: engine.TemplateLibrary) -> engine.Scenario:
    """Loads the scenario as the engine would and adds the drafting checks. Raises ConfigurationError."""
    scenario = engine.load_text(text, source)
    engine.check_templates(scenario, templates, source)
    for index, step in enumerate(scenario.steps + (scenario.cleanup or [])):
        if step.tls is not None or step.local_certificates is not None:
            raise ConfigurationError(f"{source}: step {index + 1}: drafts may only use request and sca steps")
        r = step.request
        if r is not None and (r.target != "xs2a" or "://" in r.path):
            # The scenario will later run against real banks: it must only talk to the bank's own XS2A endpoint.
            raise ConfigurationError(f"{source}: step {index + 1}: requests must go to the bank's XS2A API (a relative path or a saved link), not '{r.path}'")
    requests = [st.request for st in scenario.steps + (scenario.cleanup or []) if st.request is not None]
    moves_money = "vars.pis." in text or any(_PAYMENT_PATH.search(r.path) or (r.body_template or "").lower().startswith("payment.")
                                             for r in requests)
    if moves_money and not any(req.startswith("variables.pis.") for req in scenario.requires or []):
        # A payment moves money: it may only run where the profile names the bank's sandbox test accounts.
        raise ConfigurationError(f"{source}: payment scenarios need `requires:` with a variables.pis.* test account "
                                 "(e.g. variables.pis.creditorIban), so they only run where test accounts are configured")
    return scenario


def expectation_notes(scenario: engine.Scenario, rule: RuleRow) -> list[str]:
    """Notes when the scenario does not expect what the rule's check says (the reviewer decides if that is fine)."""
    check = rule.check_json or {}
    statuses, codes = set(), set()
    for step in scenario.steps:
        if step.expect is None:
            continue
        status = step.expect.status
        statuses.update(int(s) for s in (status if isinstance(status, list) else [status]) if str(s).isdigit())
        if step.expect.error_code:
            codes.add(step.expect.error_code)
    notes = []
    if (want := set(check.get("expectStatus") or [])) and not want & statuses:
        notes.append(f"The rule expects status {', '.join(map(str, sorted(want)))}; the scenario never expects it.")
    if (want := set(check.get("expectErrorCodes") or [])) and not (want & codes or "*" in codes):
        notes.append(f"The rule expects error code {', '.join(sorted(want))}; the scenario never expects it.")
    return notes


def dry_run_record(result: ScenarioResult, sca_errors: list[str], masker) -> dict:
    """The dry run as stored and shown: outcomes, failures and the HTTP status of each step (masked)."""
    def step(s):
        x = s.exchange or (s.exchanges[-1] if s.exchanges else None)
        return {"name": s.name, "outcome": str(s.outcome), "failures": s.failures, "warnings": s.warnings,
                "request": f"{x.method} {masker.mask_url(x.url)}" if x else None,
                "status": x.status_code if x else None,
                "response": (masker.mask_body(x.response_body) or "")[:600] if x else None}
    return {"outcome": str(result.outcome), "reason": result.reason, "steps": [step(s) for s in result.steps],
            "cleanup": [step(s) for s in result.cleanup], "scaErrors": sca_errors}


def describe_failure(record: dict) -> str:
    lines = [f"Outcome: {record['outcome']}" + (f" — {record['reason']}" if record.get("reason") else "")]
    for s in record["steps"]:
        lines.append(f"- {s['name']}: {s['outcome']}" + (f" ({s['request']} -> {s['status']})" if s.get("request") else ""))
        lines += [f"    failure: {f}" for f in s["failures"]]
        if s["outcome"] != str(Outcome.PASSED) and s.get("response"):
            lines.append(f"    response body: {s['response']}")
    lines += [f"- SCA login error: {e}" for e in record.get("scaErrors") or []]
    return "\n".join(lines)


# ---- drafting --------------------------------------------------------------------------------------------------

class ScenarioDrafter:
    """dry_run: an object with run(scenario) -> (ScenarioResult, sca_errors), e.g. aqt.dryrun.MockDryRun."""

    def __init__(self, store, chats: ChatFactory, scenarios_directory: str, dry_run):
        self._store, self._chats, self._dir, self._dry_run = store, chats, scenarios_directory, dry_run

    def draft(self, rule_id: str, triggered_by: str | None = None) -> ScenarioDraftRow:
        rule = self._store.get_rule(rule_id)
        if rule is None:
            raise ValueError(f"No rule {rule_id}")
        if rule.status != "approved":
            raise ValueError(f"{rule.id} is {rule.status}; scenarios are drafted only from approved rules (approve it on the Rules page first).")
        clause = self._store.get_clause(rule.clause_key)
        templates = engine.TemplateLibrary.load(self._dir)
        existing = engine.load_all(self._dir)

        suite, prefix = suite_and_prefix(rule)
        taken = {s.id.lower() for s in existing} | self._store.drafted_scenario_ids()
        settings = self._chats.describe(opt.SCENARIO_DRAFTING)
        model = f"{settings.provider}:{settings.model}"
        identity = Identity(id=next_scenario_id(prefix, taken), suite=suite, severity=_SEVERITY.get(rule.level, "Major"),
                            spec_ref=f"{rule.standard} {rule.version} {rule.clause_number}" + (f" {clause.title}" if clause else ""),
                            origin=f"ai:{model} from rule {rule.id}", rule_id=rule.id)
        covering = [s for s in existing if rule.id in (s.rules or [])]

        system = SYSTEM_PROMPT + "\n" + self._reference_material(PIS_EXAMPLES if rule.service == "PIS" else EXAMPLES)
        messages = [{"role": "user", "content": self._user_message(rule, clause, covering)}]
        started = time.monotonic()
        notes: list[str] = []
        call_ids: list[uuid.UUID] = []
        yaml_text, record, gap, status = "", None, None, "failed"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            call_id = uuid.uuid4()
            call_ids.append(call_id)
            try:
                result = self._chats.create(opt.SCENARIO_DRAFTING, CallContext(None, triggered_by, call_id)).complete(system, messages)
            except (ProviderError, TimeoutError) as ex:
                notes.append(f"Attempt {attempt}: model call failed: {type(ex).__name__}: {ex}")
                break
            if result.finish_reason == "refusal":
                notes.append(f"Attempt {attempt}: the model declined to answer (refusal).")
                break
            messages.append({"role": "assistant", "content": result.text})
            body, gap = extract(result.text)

            try:
                tree = yamljson.parse(body, identity.id)
                if not isinstance(tree, dict):
                    raise ConfigurationError(f"{identity.id}: the answer is not a YAML mapping")
                yaml_text = to_yaml(apply_identity(tree, identity), rule)
                scenario = check_scenario(yaml_text, identity.id, templates)
            except ConfigurationError as ex:
                notes.append(f"Attempt {attempt}: does not load: {ex}")
                messages.append({"role": "user", "content": f"The scenario does not load:\n{ex}\nFix it and reply with the whole scenario in one ```yaml block."})
                gap = None
                continue

            sca_errors: list[str] = []
            run_result, sca_errors = self._dry_run.run(scenario)
            record = dry_run_record(run_result, sca_errors, self._chats.masker)
            outcome = run_result.outcome
            if outcome == Outcome.PASSED:
                notes.append(f"Attempt {attempt}: loads and passes on MockBank.")
                status, gap = "draft", None
                break
            if outcome == Outcome.SKIPPED:
                notes.append(f"Attempt {attempt}: loads; skipped on MockBank ({run_result.reason}), so not verified by a dry run.")
                status, gap = "draft", None
                break
            if gap and outcome == Outcome.FAILED and not any(st.is_tool_error for st in run_result.steps):
                # Only a failed expectation can be a MockBank gap; a tool error (bad variable, no response) is the scenario's.
                notes.append(f"Attempt {attempt}: fails on MockBank; the model says MockBank does not implement the rule: {gap}")
                status = "draft"
                break
            notes.append(f"Attempt {attempt}: {outcome} on MockBank: {run_result.reason or 'see the dry run'}")
            messages.append({"role": "user", "content":
                             "The scenario was run against the test bank (MockBank) and did not pass:\n" + describe_failure(record) +
                             "\nIf the scenario is wrong, fix it and reply with the whole scenario in one ```yaml block. If the scenario is "
                             "right for the rule and the test bank does not implement the rule, reply with a MOCK_GAP line and the scenario unchanged."})

        if status == "draft" and yaml_text:
            notes += expectation_notes(engine.load_text(yaml_text, identity.id), rule)
        now = datetime.now(timezone.utc)
        draft = ScenarioDraftRow(
            id=uuid.uuid4(), rule_id=rule.id, scenario_id=identity.id, suite=suite,
            title=_title(yaml_text) or rule.title, yaml=yaml_text, status=status, attempts=len(call_ids),
            drafted_by=triggered_by or "ai", created_utc=now, updated_utc=now, check_notes=notes,
            dry_run_outcome=record["outcome"] if record else None, dry_run=record, mock_gap=gap, model=model, ai_call_ids=call_ids)
        draft.check_notes.append(f"Drafting took {int(time.monotonic() - started)} s.")
        self._store.add_scenario_draft(draft)
        return draft

    def _reference_material(self, examples=EXAMPLES) -> str:
        parts = ["TEMPLATES (scenarios/_templates):"]
        templates_dir = os.path.join(self._dir, engine.TEMPLATES_FOLDER)
        for name in sorted(os.listdir(templates_dir)):
            if name.endswith(".yaml"):
                with open(os.path.join(templates_dir, name), encoding="utf-8") as f:
                    parts += [f"--- {name}", f.read()]
        for example in examples:
            with open(os.path.join(self._dir, example), encoding="utf-8") as f:
                parts += [f"EXAMPLE SCENARIO {example}:", f.read()]
        return "\n".join(parts)

    @staticmethod
    def _user_message(rule: RuleRow, clause, covering: list[engine.Scenario]) -> str:
        check = rule.check_json or {}
        lines = [f"RULE {rule.id} ({rule.level}, {rule.service}, area {rule.area})", f"Title: {rule.title}",
                 f"Requirement: {rule.requirement}", f"Quote from the standard: {rule.quote}"]
        if rule.quote_english:
            lines.append(f"Quote in English (AI translation): {rule.quote_english}")
        if rule.operation:
            lines.append(f"Operation: {rule.operation}")
        if check:
            lines.append(f"How to check ({check.get('kind', 'manual')}): {check.get('description', '')}")
            if check.get("expectStatus"):
                lines.append(f"Expected status: {', '.join(map(str, check['expectStatus']))}")
            if check.get("expectErrorCodes"):
                lines.append(f"Expected error codes: {', '.join(check['expectErrorCodes'])}")
        if clause is not None:
            text = clause.text if len(clause.text) <= 6000 else clause.text[:6000] + " …"
            lines += ["", f"CLAUSE {clause.standard} {clause.number} {clause.title}:", text]
        if covering:
            lines += ["", "SCENARIOS THAT ALREADY CHECK THIS RULE (check a different case or fewer things):"]
            lines += [f"- {s.id}: {s.title}" for s in covering]
        return "\n".join(lines) + "\n"


def _title(yaml_text: str) -> str | None:
    match = re.search(r"^title:\s*(.+)$", yaml_text, re.M)
    return match.group(1).strip().strip("'\"") if match else None


# ---- review ----------------------------------------------------------------------------------------------------

class ScenarioReviewError(Exception):
    """A review action that is not allowed (shown to the reviewer as is)."""


class ScenarioReview:
    """The human side of scenario drafts. Approving writes scenarios/<suite>/<id>-<title>.yaml (checked again by
    loading the whole scenarios folder) and links the scenario to its rule; rejecting needs a reason."""

    def __init__(self, store, scenarios_directory: str, dry_run=None, masker=None):
        self._store, self._dir, self._dry_run, self._masker = store, scenarios_directory, dry_run, masker

    def approve(self, draft_id, reviewer: str, note: str | None = None) -> ScenarioDraftRow:
        draft, expected = self._load(draft_id, reviewer)
        _require(draft.status == "draft", f"Only drafts can be approved; this one is {draft.status}.")
        rule = self._store.get_rule(draft.rule_id)
        _require(rule is not None and rule.status == "approved", f"Rule {draft.rule_id} is no longer approved; reopen or re-approve it first.")
        try:
            scenario = check_scenario(draft.yaml, draft.scenario_id, engine.TemplateLibrary.load(self._dir))
        except ConfigurationError as ex:
            raise ScenarioReviewError(f"The draft no longer loads: {ex}") from ex
        existing = {s.id.lower(): s.source_path for s in engine.load_all(self._dir)}
        _require(scenario.id.lower() not in existing, f"A scenario {scenario.id} already exists: {existing.get(scenario.id.lower())}")

        directory = os.path.join(self._dir, draft.suite)
        path = os.path.join(directory, f"{draft.scenario_id}-{slug(draft.title)}.yaml")
        _require(not os.path.exists(path), f"{path} already exists")
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(draft.yaml)
        try:
            engine.load_all(self._dir)  # the whole set must still load (ids unique, templates present)
            now = datetime.now(timezone.utc)
            draft.status, draft.reviewed_by, draft.reviewed_utc, draft.review_note = "approved", reviewer.strip(), now, note
            draft.updated_utc, draft.file_path = now, os.path.relpath(path, os.path.dirname(os.path.abspath(self._dir)))
            self._store.update_scenario_draft(draft, expected)
        except (ConfigurationError, ValueError) as ex:
            os.remove(path)
            raise ScenarioReviewError(str(ex)) from ex
        self._store.link_rule_scenario(draft.rule_id, draft.scenario_id, reviewer.strip())
        return draft

    def reject(self, draft_id, reviewer: str, note: str) -> ScenarioDraftRow:
        draft, expected = self._load(draft_id, reviewer)
        _require(bool((note or "").strip()), "Say why the draft is rejected.")
        _require(draft.status in ("draft", "failed"), f"Only open drafts can be rejected; this one is {draft.status}.")
        now = datetime.now(timezone.utc)
        draft.status, draft.reviewed_by, draft.reviewed_utc, draft.review_note, draft.updated_utc = "rejected", reviewer.strip(), now, note, now
        self._save(draft, expected)
        return draft

    def edit(self, draft_id, reviewer: str, yaml_text: str) -> ScenarioDraftRow:
        """A reviewer's fix of the YAML: code-owned fields are kept, the draft is checked and dry-run again."""
        draft, expected = self._load(draft_id, reviewer)
        _require(draft.status in ("draft", "failed"), f"Only open drafts can be edited; this one is {draft.status}.")
        rule = self._store.get_rule(draft.rule_id)
        try:
            tree = yamljson.parse(yaml_text, draft.scenario_id)
            if not isinstance(tree, dict):
                raise ConfigurationError("a scenario must be a YAML mapping")
            old = yamljson.parse(draft.yaml, draft.scenario_id) if draft.yaml.strip() else {}
            identity = Identity(id=draft.scenario_id, suite=draft.suite, severity=(old or {}).get("severity") or _SEVERITY.get(rule.level, "Major"),
                                spec_ref=(old or {}).get("specRef") or f"{rule.standard} {rule.version} {rule.clause_number}",
                                origin=(old or {}).get("origin") or f"ai:{draft.model} from rule {rule.id}", rule_id=rule.id)
            new_yaml = to_yaml(apply_identity(tree, identity), rule)
            scenario = check_scenario(new_yaml, draft.scenario_id, engine.TemplateLibrary.load(self._dir))
        except ConfigurationError as ex:
            raise ScenarioReviewError(f"The edited scenario does not load: {ex}") from ex

        notes = [n for n in draft.check_notes if not n.startswith("The rule expects")]
        notes.append(f"Edited by {reviewer.strip()}.")
        if self._dry_run is not None:
            result, sca_errors = self._dry_run.run(scenario)
            draft.dry_run = dry_run_record(result, sca_errors, self._masker or _identity_masker())
            draft.dry_run_outcome = draft.dry_run["outcome"]
            notes.append(f"Dry run after the edit: {draft.dry_run_outcome}.")
        notes += expectation_notes(scenario, rule)
        draft.yaml, draft.title, draft.status, draft.check_notes = new_yaml, scenario.title, "draft", notes
        draft.updated_utc = datetime.now(timezone.utc)
        self._save(draft, expected)
        return draft

    def _load(self, draft_id, reviewer):
        _require(bool((reviewer or "").strip()), "A reviewer name is required.")
        draft = self._store.get_scenario_draft(draft_id)
        _require(draft is not None, f"No scenario draft {draft_id}")
        return draft, draft.updated_utc

    def _save(self, draft, expected):
        try:
            self._store.update_scenario_draft(draft, expected)
        except ValueError as ex:
            raise ScenarioReviewError(str(ex)) from ex


def _identity_masker():
    from ..store.masking import DEFAULT
    return DEFAULT


def _require(condition: bool, message: str):
    if not condition:
        raise ScenarioReviewError(message)
