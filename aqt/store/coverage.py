"""
Coverage: which approved rules are verified by which scenarios, and how those scenarios last did on each bank.
Computed in code from the rules table, the `rules:` field of the scenario files and the stored runs.
"""

from dataclasses import dataclass, field

from .. import scenarios as engine


@dataclass
class ScenarioCoverage:
    id: str
    title: str
    file: str | None             # None: linked in the store, but no such scenario file
    results: dict[str, tuple[str, str]] = field(default_factory=dict)   # bank BIC -> (outcome, run id)


@dataclass
class RuleCoverage:
    rule_id: str
    title: str
    level: str
    service: str
    status: str
    clause_key: str
    scenarios: list[ScenarioCoverage]
    open_drafts: int = 0

    @property
    def covered(self) -> bool:
        return any(s.file for s in self.scenarios)


@dataclass
class Coverage:
    rules: list[RuleCoverage]
    banks: list[str]
    # Scenario files that name a rule that is missing or not approved.
    problems: list[str]

    @property
    def covered_count(self) -> int:
        return sum(r.covered for r in self.rules if r.status == "approved")

    @property
    def approved_count(self) -> int:
        return sum(r.status == "approved" for r in self.rules)


def scenario_rule_problems(store, loaded: list[engine.Scenario]) -> list[str]:
    """Scenarios whose `rules:` name a rule that is not in the store or not approved (used by validate and coverage)."""
    problems = []
    for s in loaded:
        for rule_id in s.rules or []:
            rule = store.get_rule(rule_id)
            if rule is None:
                problems.append(f"{s.id}: rule {rule_id} is not in the store")
            elif rule.status != "approved":
                problems.append(f"{s.id}: rule {rule_id} is {rule.status}, not approved")
    return problems


def compute(store, scenarios_directory: str, include_drafts: bool = False) -> Coverage:
    loaded = engine.load_all(scenarios_directory)
    by_rule: dict[str, list[engine.Scenario]] = {}
    for s in loaded:
        for rule_id in s.rules or []:
            by_rule.setdefault(rule_id, []).append(s)
    by_id = {s.id: s for s in loaded}

    rules = [r for r in store.list_rules() if r.status == "approved" or (include_drafts and r.status == "draft")]
    linked = {sid for r in rules for sid in r.scenarios} | {s.id for group in by_rule.values() for s in group}
    outcomes = store.latest_outcomes(sorted(linked))
    banks = sorted({bank for (_, bank) in outcomes})
    open_drafts: dict[str, int] = {}
    for d in store.list_scenario_drafts(status="draft"):
        open_drafts[d.rule_id] = open_drafts.get(d.rule_id, 0) + 1

    result = []
    for r in sorted(rules, key=lambda r: r.id):
        ids = list(dict.fromkeys([s.id for s in by_rule.get(r.id, [])] + list(r.scenarios)))
        items = []
        for sid in ids:
            s = by_id.get(sid)
            items.append(ScenarioCoverage(sid, s.title if s else "(no scenario file)", s.source_path if s else None,
                                          {bank: outcomes[(sid, bank)][:2] for bank in banks if (sid, bank) in outcomes}))
        result.append(RuleCoverage(r.id, r.title, r.level, r.service, r.status, r.clause_key, items, open_drafts.get(r.id, 0)))
    return Coverage(result, banks, scenario_rule_problems(store, loaded))
