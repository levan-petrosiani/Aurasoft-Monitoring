"""
Rules from the standards: AI drafts them from one clause; code decides what reaches the review queue; only a person
(RuleBook) approves, edits, rejects or reopens, always recorded in rule_reviews with the fields before and after.

Checks in code: the quote must occur in the clause (else the rule is stored as `invalid`), a level not backed by the
quote's wording is flagged, and a near-duplicate (close in meaning AND naming the same identifiers) is flagged.
"""

import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources

from ..standards import check_quote
from ..store.entities import ClauseRow, RuleReviewRow, RuleRow
from . import options as opt
from .chat import CallContext, ChatFactory, ProviderError

# Cosine distance under which two rules are flagged as possibly the same (bge-m3).
DUPLICATE_DISTANCE = 0.12

LEVELS = ("MUST", "SHOULD", "MAY")
SERVICES = ("AIS", "PIS", "COMMON")
AREAS = ("CON", "ACC", "TRX", "PIS", "PST", "PCN", "SBK", "AUTH", "SIG", "TLS", "ERR", "HDR", "GEN")

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)


def _prompt(name: str) -> str:
    return resources.files("aqt.ai").joinpath("prompts", name).read_text(encoding="utf-8")


DRAFTS_SCHEMA = json.loads(_prompt("rule_drafts.schema.json"))
SYSTEM_PROMPT = _prompt("rule_drafting.txt") + _prompt("rule_drafts.schema.json")


# ---- normative level -------------------------------------------------------------------------------------------

# RFC 2119 keywords in English and their usual equivalents in the Georgian guides. "MUST NOT"/"is not allowed" are MUST.
# The lookbehinds keep "არასავალდებულო" (optional) from counting as "სავალდებულო"/"ვალდებულ" (mandatory/obliged).
_MUST = re.compile(r"\b(must|shall|required|mandatory|has to|have to|is not allowed|not permitted)\b|აუცილებელ|უნდა|(?<!არა)სავალდებულო|(?<!არასა)ვალდებულ|დაუშვებელ|აკრძალულ", re.I)
_SHOULD = re.compile(r"\b(should|recommended)\b|უმჯობესია|რეკომენდებ|სასურველია", re.I)
_MAY = re.compile(r"\b(may|optional|optionally|can)\b|შეიძლება|შესაძლებელ|უფლება აქვს|არასავალდებულო", re.I)


def supported_levels(quote: str) -> set[str]:
    levels = set()
    if _MUST.search(quote):
        levels.add("MUST")
    if _SHOULD.search(quote):
        levels.add("SHOULD")
    if _MAY.search(quote):
        levels.add("MAY")
    return levels


def question_level(level: str, quote: str) -> str | None:
    """A note when the drafted level is not backed by the quote's wording; None when it is."""
    supported = supported_levels(quote)
    if level in supported:
        return None
    if not supported:
        return (f"The quote has no requirement keyword (must/shall/should/may or Georgian equivalent); "
                f"check that it is normative and that {level} is right.")
    return f"Drafted as {level}, but the quote's wording suggests {' or '.join(sorted(supported))}."


# ---- identifiers and duplicates --------------------------------------------------------------------------------

_IDENTIFIER = re.compile(r"\"[^\"]+\"|'[^']+'|\b[a-z]+[A-Z]\w*\b|\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b|\b[1-5]\d\d\b|/[\w/{}.-]+")


def identifiers(text: str) -> set[str]:
    """Identifiers a requirement names: quoted terms, camelCase fields, UPPER_CASE codes, HTTP statuses, paths."""
    return {m.group().strip("\"'").lower() for m in _IDENTIFIER.finditer(text)}


def same_identifiers(a: str, b: str) -> bool:
    return identifiers(a) == identifiers(b)


def cosine_distance(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 1 - dot / (na * nb) if na and nb else 1.0


def id_prefix(standard: str) -> str:
    """BG-IG -> BG, GE-XS2A/GE-JWS/GE-IAA -> GE, NBG-80-04 -> NBG."""
    return standard.split("-")[0].upper()


# ---- drafting --------------------------------------------------------------------------------------------------

@dataclass
class DraftResult:
    clause_key: str
    rules: list[RuleRow]
    skipped: str | None
    status: str                 # ok | failed
    error: str | None
    duration_ms: int


def parse_answer(text: str) -> dict:
    cleaned = _THINK.sub("", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The answer contains no JSON object")
    data = json.loads(cleaned[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("The JSON is not an object")
    return data


def user_message(clause: ClauseRow, ancestors: list[ClauseRow], existing: list[RuleRow]) -> str:
    lines = [f"STANDARD {clause.standard} version {clause.version} · language {'English' if clause.language == 'en' else 'Georgian'}"]
    if ancestors:
        lines.append("SECTION PATH: " + " › ".join(f"{a.number} {a.title}" for a in ancestors))
    lines.append(f"CLAUSE {clause.number} {clause.title}{f' (page {clause.page})' if clause.page else ''}")
    lines += ["", "CLAUSE TEXT:", clause.text, ""]
    lines.append("EXISTING RULES (do not repeat):" if existing else "EXISTING RULES: none")
    lines += [f"- {r.id} [{r.status}] {r.level}: {r.requirement}" for r in existing]
    return "\n".join(lines) + "\n"


def snapshot(r: RuleRow) -> dict:
    """The reviewable fields of a rule, as stored in rule_reviews.before/after."""
    return {"status": r.status, "title": r.title, "level": r.level, "service": r.service, "area": r.area,
            "requirement": r.requirement, "quote": r.quote, "quoteEnglish": r.quote_english,
            "operation": r.operation, "checkJson": json.dumps(r.check_json, ensure_ascii=False) if r.check_json else None}


class RuleDrafter:
    def __init__(self, store, chats: ChatFactory):
        self._store, self._chats = store, chats

    def draft(self, clause_key: str, triggered_by: str | None = None) -> DraftResult:
        started = time.monotonic()
        elapsed = lambda: int((time.monotonic() - started) * 1000)  # noqa: E731
        clause = self._store.get_clause(clause_key)
        if clause is None:
            raise ValueError(f"Clause '{clause_key}' is not in the store (aqt standards ingest)")
        if not clause.text.strip():
            return DraftResult(clause_key, [], "The clause has no text of its own (only sub-sections).", "ok", None, elapsed())

        ancestors = self._store.get_ancestors(clause)
        existing = [r for r in self._store.list_rules(clause_key=clause_key) if r.status in ("draft", "approved")]
        settings = self._chats.describe(opt.RULE_DRAFTING)
        messages = [{"role": "user", "content": user_message(clause, ancestors, existing)}]

        answer, call_id, error = None, None, None
        for _ in range(2):
            call_id = uuid.uuid4()
            client = self._chats.create(opt.RULE_DRAFTING, CallContext(None, triggered_by, call_id))
            try:
                result = client.complete(SYSTEM_PROMPT, messages, DRAFTS_SCHEMA if settings.provider == opt.OLLAMA else None)
            except (ProviderError, TimeoutError) as ex:
                return DraftResult(clause_key, [], None, "failed", f"{type(ex).__name__}: {ex}", elapsed())
            if result.finish_reason == "refusal":
                return DraftResult(clause_key, [], None, "failed", "The model declined to answer (refusal).", elapsed())
            try:
                answer = parse_answer(result.text)
                break
            except ValueError as ex:
                error = f"Unreadable answer: {ex}"
                messages += [{"role": "assistant", "content": result.text},
                             {"role": "user", "content": f"Your answer could not be read as the required JSON ({ex}). Reply again with only the JSON object."}]
        if answer is None:
            return DraftResult(clause_key, [], None, "failed", error, elapsed())

        rules = self._check_and_number([d for d in answer.get("rules") or [] if isinstance(d, dict)], clause, settings, call_id)
        now = datetime.now(timezone.utc)
        self._store.add_rules(rules, [RuleReviewRow(
            rule_id=r.id, time_utc=now, reviewer=triggered_by or "ai", action="drafted",
            note="Failed the automatic checks: " + " ".join(r.check_notes) if r.status == "invalid" else None,
            after=snapshot(r)) for r in rules])
        return DraftResult(clause_key, rules, answer.get("skipped") or None, "ok", None, elapsed())

    def _check_and_number(self, drafts: list[dict], clause: ClauseRow, settings, call_id) -> list[RuleRow]:
        prefix = id_prefix(clause.standard)
        next_number: dict[str, int] = {}
        now = datetime.now(timezone.utc)
        embeddings_ok = True
        result: list[RuleRow] = []
        vectors: dict[str, list[float]] = {}

        for d in drafts:
            notes: list[str] = []
            level = _pick(str(d.get("level", "")).upper(), LEVELS, "MUST", "level", notes)
            service = _pick(str(d.get("service", "")).upper(), SERVICES, "COMMON", "service", notes)
            area = _pick(str(d.get("area", "")).upper(), AREAS, "GEN", "area", notes)
            quote = str(d.get("quote") or "").strip()
            requirement = str(d.get("requirement") or "").strip()
            quote_english = str(d.get("quoteEnglish") or "").strip()

            problem = check_quote(quote, clause.text)
            invalid = problem is not None or not requirement
            notes.append("Quote found in the clause." if problem is None else "Quote check failed: " + problem)
            if not requirement:
                notes.append("No requirement text.")
            if problem is None and (level_note := question_level(level, quote)):
                notes.append(level_note)
            if clause.language != "en" and not quote_english:
                notes.append("No English translation of the quote.")

            key = f"{prefix}-{area}"
            if key not in next_number:
                next_number[key] = self._store.max_rule_number(key)
            next_number[key] += 1

            check = d.get("check") if isinstance(d.get("check"), dict) else {}
            rule = RuleRow(
                id=f"{key}-{next_number[key]:03d}", status="invalid" if invalid else "draft", title=str(d.get("title") or "").strip(),
                level=level, service=service, area=area, requirement=requirement, standard=clause.standard,
                version=clause.version, clause_number=clause.number, clause_key=clause.key, quote=quote,
                drafted_by="ai", created_utc=now, updated_utc=now,
                quote_english=None if clause.language == "en" or not quote_english else quote_english,
                operation=str(d.get("operation") or "").strip() or None,
                check_json={"kind": str(check.get("kind") or "manual"), "description": str(check.get("description") or ""),
                            "expectStatus": [int(x) for x in check.get("expectStatus") or [] if str(x).isdigit()],
                            "expectErrorCodes": [str(x) for x in check.get("expectErrorCodes") or []]},
                notes=str(d.get("notes") or "").strip() or None, model=f"{settings.provider}:{settings.model}", ai_call_id=call_id)

            if not invalid and embeddings_ok:
                try:
                    vector = self._chats.embed([rule.requirement])[0]
                    rule.embedding = vector
                    vectors[rule.id] = vector
                    # Close in meaning AND about the same things: embeddings alone rate "accept availableAccounts" and
                    # "accept allPsd2" as near-identical, so identifiers (fields, codes, statuses, paths) must match too.
                    candidates = list(self._store.find_similar_rules(vector, DUPLICATE_DISTANCE))
                    candidates += [(r, cosine_distance(vector, vectors[r.id])) for r in result if r.id in vectors]
                    candidates = sorted((c for c in candidates if c[1] <= DUPLICATE_DISTANCE and same_identifiers(rule.requirement, c[0].requirement)),
                                        key=lambda c: c[1])
                    if candidates:
                        other, distance = candidates[0]
                        rule.similar_to = other.id
                        notes.append(f"Possible duplicate of {other.id} ({other.status}, similarity {1 - distance:.2f}): {other.requirement}")
                except (ProviderError, TimeoutError, KeyError) as ex:
                    embeddings_ok = False  # local embedding model unavailable: skip the duplicate check, say so once
                    notes.append(f"Duplicate check skipped: local embedding model unavailable ({ex}).")

            rule.check_notes = notes
            result.append(rule)
        return result


def _pick(value: str, allowed, fallback: str, what: str, notes: list[str]) -> str:
    if value in allowed:
        return value
    notes.append(f'Unknown {what} "{value}" replaced by {fallback}.')
    return fallback


# ---- review ----------------------------------------------------------------------------------------------------

@dataclass
class RuleEdit:
    """Fields a reviewer may change before approving."""
    title: str | None = None
    level: str | None = None
    service: str | None = None
    area: str | None = None
    requirement: str | None = None
    quote: str | None = None
    quote_english: str | None = None
    operation: str | None = None
    check_json: dict | None = None


class ReviewError(Exception):
    """A review action that is not allowed (shown to the reviewer as is)."""


class RuleBook:
    """The human side of rules. Only these actions change a rule's status; each needs a reviewer name and writes a
    rule_reviews row. An edited quote is checked against the clause again."""

    def __init__(self, store):
        self._store = store

    def approve(self, rule_id: str, reviewer: str, note: str | None = None) -> RuleRow:
        def change(r):
            _require(r.status == "draft", f"Only drafts can be approved; {r.id} is {r.status}.")
            r.status = "approved"
        return self._change(rule_id, reviewer, "approved", note, change)

    def reject(self, rule_id: str, reviewer: str, note: str) -> RuleRow:
        def change(r):
            _require(bool((note or "").strip()), "Say why the rule is rejected (it helps the next draft).")
            _require(r.status in ("draft", "approved"), f"{r.id} is {r.status}; only drafts and approved rules can be rejected.")
            r.status = "rejected"
        return self._change(rule_id, reviewer, "rejected", note, change)

    def reopen(self, rule_id: str, reviewer: str, note: str | None = None) -> RuleRow:
        def change(r):
            _require(r.status in ("approved", "rejected"), f"{r.id} is {r.status}; only approved or rejected rules can be reopened.")
            r.status = "draft"
        return self._change(rule_id, reviewer, "reopened", note, change)

    def retire(self, rule_id: str, reviewer: str, note: str) -> RuleRow:
        def change(r):
            _require(r.status == "approved", f"Only approved rules can be retired; {r.id} is {r.status}.")
            r.status = "retired"
        return self._change(rule_id, reviewer, "retired", note, change)

    def edit(self, rule_id: str, reviewer: str, edit: RuleEdit, note: str | None = None) -> RuleRow:
        """Edits a draft (or an invalid draft). Approved rules must be reopened first, so a change is reviewed again."""
        clause = None
        if edit.quote is not None:
            rule = self._store.get_rule(rule_id)
            if rule is None:
                raise ReviewError(f"No rule {rule_id}")
            clause = self._store.get_clause(rule.clause_key)
            if clause is None:
                raise ReviewError(f"Clause {rule.clause_key} is not in the store")

        def change(r):
            _require(r.status in ("draft", "invalid"), f"{r.id} is {r.status}; reopen it before editing.")
            if edit.quote is not None and (problem := check_quote(edit.quote, clause.text)):
                raise ReviewError("The edited quote is not in the clause: " + problem)
            r.title = edit.title.strip() if edit.title is not None else r.title
            r.level = _normalize(edit.level, LEVELS, "level") if edit.level is not None else r.level
            r.service = _normalize(edit.service, SERVICES, "service") if edit.service is not None else r.service
            r.area = _normalize(edit.area, AREAS, "area") if edit.area is not None else r.area
            r.requirement = edit.requirement.strip() if edit.requirement is not None else r.requirement
            r.quote = edit.quote.strip() if edit.quote is not None else r.quote
            r.quote_english = edit.quote_english if edit.quote_english is not None else r.quote_english
            r.operation = edit.operation if edit.operation is not None else r.operation
            r.check_json = edit.check_json if edit.check_json is not None else r.check_json
            if r.status == "invalid" and edit.quote is not None:
                # A fixed quote makes an invalid draft reviewable.
                r.status = "draft"
                r.check_notes = [n for n in r.check_notes if not n.startswith("Quote check failed")] + ["Quote fixed by the reviewer and found in the clause."]

        return self._change(rule_id, reviewer, "edited", note, change, clear_embedding=edit.requirement is not None)

    def _change(self, rule_id, reviewer, action, note, change, clear_embedding=False) -> RuleRow:
        _require(bool((reviewer or "").strip()), "A reviewer name is required.")
        rule = self._store.get_rule(rule_id)
        if rule is None:
            raise ReviewError(f"No rule {rule_id}")
        expected = rule.updated_utc
        before = snapshot(rule)
        change(rule)
        now = datetime.now(timezone.utc)
        rule.updated_utc = now
        if action in ("approved", "rejected", "retired"):
            rule.reviewed_by, rule.reviewed_utc, rule.review_note = reviewer.strip(), now, note
        try:
            self._store.update_rule(rule, expected, RuleReviewRow(rule_id=rule_id, time_utc=now, reviewer=reviewer.strip(),
                                                                  action=action, note=note, before=before, after=snapshot(rule)),
                                    clear_embedding=clear_embedding)
        except ValueError as ex:
            raise ReviewError(str(ex)) from ex
        return rule

    def export(self, rules_directory: str) -> tuple[int, int]:
        """Writes approved rules to rules/<prefix>/<id>.yaml and removes files of rules that are no longer approved, so the
        approved set is versioned in git next to the scenarios that will reference it."""
        approved = self._store.list_rules("approved")
        keep = set()
        for rule in approved:
            directory = os.path.join(rules_directory, id_prefix(rule.standard).lower())
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, rule.id + ".yaml")
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(to_yaml(rule))
            keep.add(os.path.abspath(path))
        removed = 0
        if os.path.isdir(rules_directory):
            for directory, _, files in os.walk(rules_directory):
                for name in files:
                    path = os.path.abspath(os.path.join(directory, name))
                    if name.endswith(".yaml") and not name.startswith("_") and path not in keep:
                        os.remove(path)
                        removed += 1
        return len(approved), removed


def to_yaml(r: RuleRow) -> str:
    q = lambda v: json.dumps(v or "", ensure_ascii=False)  # noqa: E731 - JSON strings are valid YAML scalars
    check = r.check_json or {}
    lines = ["# Exported from the rule store (aqt rules export). Edit rules in the portal, not here.",
             f"id: {r.id}", f"title: {q(r.title)}", f"status: {r.status}", f"level: {r.level}", f"service: {r.service}",
             f"area: {r.area}", "source:", f"  standard: {r.standard}", f"  version: {q(r.version)}",
             f"  clause: {q(r.clause_number)}", f"  quote: {q(r.quote)}"]
    if r.quote_english:
        lines.append(f"  quoteEnglish: {q(r.quote_english)}  # AI translation")
    lines.append(f"requirement: {q(r.requirement)}")
    if r.operation:
        lines += ["appliesTo:", f"  operation: {q(r.operation)}"]
    if check:
        lines += ["check:", f"  kind: {check.get('kind', 'manual')}", f"  description: {q(check.get('description'))}"]
        if check.get("expectStatus"):
            lines.append(f"  expectStatus: [{', '.join(str(s) for s in check['expectStatus'])}]")
        if check.get("expectErrorCodes"):
            lines.append(f"  expectErrorCodes: [{', '.join(check['expectErrorCodes'])}]")
    stamp = lambda dt: dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""  # noqa: E731
    lines += [f"scenarios: [{', '.join(r.scenarios)}]", "drafted:", f"  by: {r.drafted_by}"]
    if r.model:
        lines.append(f"  model: {q(r.model)}")
    lines += [f"  at: {stamp(r.created_utc)}", "review:", f"  by: {q(r.reviewed_by)}", f"  at: {stamp(r.reviewed_utc)}",
              f"  note: {q(r.review_note)}"]
    return "\n".join(lines) + "\n"


def _normalize(value: str, allowed, what: str) -> str:
    v = value.strip().upper()
    if v not in allowed:
        raise ReviewError(f"Unknown {what} '{value}' (use {', '.join(allowed)})")
    return v


def _require(condition: bool, message: str):
    if not condition:
        raise ReviewError(message)
