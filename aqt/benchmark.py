"""
Cross-bank comparison and latency benchmark, computed in code from saved run results (no database, no AI).

Every request a run made is a latency sample. Requests are grouped by operation ("GET /accounts/{id}/balances"):
the host, leading version segments (/0.8, /v1) and ids are removed, so the same call can be compared across banks
whose URLs differ. Per bank the last N runs are used, so the figures also show whether results are reproducible.
"""

import html
import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .results import Outcome, RunResult, format_rate, pass_rate

DEFAULT_LAST_RUNS = 5

_VERSION = re.compile(r"^(v\d+|\d+(\.\d+)*)$", re.I)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_SLOW = "Slow response"


def operation(method: str, url: str) -> str:
    """'GET https://bank/0.8/v1/accounts/acc-001/balances?x=1' -> 'GET /accounts/{id}/balances'."""
    segments = [s for s in urlsplit(url).path.split("/") if s]
    if "oauth" in segments:  # /openbanking/oauth/token and /oauth/token are the same call
        segments = segments[segments.index("oauth"):]
    while segments and _VERSION.match(segments[0]):
        segments.pop(0)
    names = ["{id}" if _UUID.match(s) or any(c.isdigit() for c in s) or len(s) > 40 else s for s in segments]
    return f"{method.upper()} /{'/'.join(names)}"


def percentile(values: list[int], p: float) -> int:
    """Nearest-rank percentile (p in 0..100) of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * p // 100))  # ceil
    return ordered[int(rank) - 1]


@dataclass
class SavedRun:
    run_id: str
    result: RunResult


@dataclass
class Latency:
    samples: list[int] = field(default_factory=list)
    errors: int = 0  # no response (connection, TLS, timeout) or a 5xx answer

    @property
    def count(self) -> int:
        return len(self.samples)

    @property
    def p50(self) -> int | None:
        return percentile(self.samples, 50) if self.samples else None

    @property
    def p95(self) -> int | None:
        return percentile(self.samples, 95) if self.samples else None

    @property
    def max(self) -> int | None:
        return max(self.samples) if self.samples else None


@dataclass
class ScenarioCell:
    """One scenario at one bank over the compared runs: the latest outcome and how often it passed."""
    latest: Outcome
    passed: int = 0
    executed: int = 0  # passed + failed + error

    @property
    def stable(self) -> bool:
        return self.executed == 0 or self.passed in (0, self.executed)


@dataclass
class Deviation:
    scenario_id: str
    title: str
    outcome: Outcome
    spec_ref: str
    detail: str


@dataclass
class BankSummary:
    bic: str
    name: str
    runs: list[str]
    passed: int
    failed: int
    errors: int
    skipped: int
    pass_rate: float
    requests: int
    latency: Latency
    slow: int
    deviations: list[Deviation]


@dataclass
class Comparison:
    banks: list[BankSummary]
    operations: list[str]
    latency: dict[tuple[str, str], Latency]                 # (bic, operation)
    scenarios: list[tuple[str, str, dict[str, ScenarioCell]]]  # (id, title, {bic: cell})
    last_runs: int

    def bank(self, bic: str) -> BankSummary:
        return next(b for b in self.banks if b.bic == bic)


def load_reports(directory: str) -> list[SavedRun]:
    """CLI runs: <directory>/<run id>/summary.json (unreadable folders are skipped)."""
    runs = []
    if not os.path.isdir(directory):
        return runs
    for name in os.listdir(directory):
        path = os.path.join(directory, name, "summary.json")
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    runs.append(SavedRun(name, RunResult.from_dict(json.load(f))))
            except (OSError, ValueError, KeyError):
                continue
    return runs


def _first_problem(scenario) -> str:
    for step in scenario.steps:
        if step.failures:
            return f"{step.name}: {step.failures[0]}"
    return scenario.reason or ""


def compare(runs: list[SavedRun], banks: list[str] | None = None, last: int = DEFAULT_LAST_RUNS) -> Comparison:
    """The last `last` runs of each bank (all banks with runs, or only `banks` by BIC), newest first."""
    wanted = {b.upper() for b in banks or []}
    by_bank: dict[str, list[SavedRun]] = {}
    for run in runs:
        bic = run.result.bank_bic.upper()
        if run.result.started_at_utc is None or (wanted and bic not in wanted):
            continue
        by_bank.setdefault(bic, []).append(run)

    summaries, latency, cells, titles = [], {}, {}, {}
    for bic in sorted(by_bank):
        selected = sorted(by_bank[bic], key=lambda r: r.result.started_at_utc, reverse=True)[:last]
        newest = selected[0].result
        overall, slow, requests = Latency(), 0, 0
        for run in selected:
            for scenario in run.result.scenarios:
                titles.setdefault(scenario.id, scenario.title)
                cell = cells.setdefault((scenario.id, bic), ScenarioCell(scenario.outcome))
                if scenario.outcome in (Outcome.PASSED, Outcome.FAILED, Outcome.ERROR):
                    cell.executed += 1
                    cell.passed += scenario.outcome == Outcome.PASSED
                for step in scenario.steps + scenario.cleanup:
                    slow += sum(w.startswith(_SLOW) for w in step.warnings)
                    for x in step.all_exchanges():
                        requests += 1
                        stats = latency.setdefault((bic, operation(x.method, x.url)), Latency())
                        if x.status_code is None or x.status_code >= 500:
                            stats.errors += 1
                            overall.errors += 1
                        if x.status_code is not None:
                            stats.samples.append(x.duration_ms)
                            overall.samples.append(x.duration_ms)
        deviations = [Deviation(s.id, s.title, s.outcome, s.spec_ref or "", _first_problem(s))
                      for s in newest.scenarios if s.outcome in (Outcome.FAILED, Outcome.ERROR)]
        summaries.append(BankSummary(
            bic, newest.bank_name, [r.run_id for r in selected], newest.count(Outcome.PASSED), newest.count(Outcome.FAILED),
            newest.count(Outcome.ERROR), newest.count(Outcome.SKIPPED), pass_rate(newest.scenarios), requests, overall, slow,
            deviations))

    operations = sorted({op for _, op in latency}, key=lambda op: (-sum((b.bic, op) in latency for b in summaries), op.split(" ", 1)[1], op))
    rows = []
    for scenario_id in sorted(titles, key=lambda i: (not i.startswith("PRE-"), i)):
        row = {b.bic: cells[(scenario_id, b.bic)] for b in summaries if (scenario_id, b.bic) in cells}
        if any(c.latest != Outcome.SKIPPED or c.executed for c in row.values()):
            rows.append((scenario_id, titles[scenario_id], row))
    return Comparison(summaries, operations, latency, rows, last)


# ---- output ------------------------------------------------------------------------------------------------------

def format_ms(value: int | None) -> str:
    return "—" if value is None else f"{value} ms"


def render_text(c: Comparison) -> str:
    lines = [f"Comparison of the last {c.last_runs} run(s) per bank", ""]
    lines.append(f"{'Bank':<12} {'Runs':>4} {'Pass rate':>9} {'Pass':>5} {'Fail':>5} {'Err':>4} {'Requests':>8} {'p50':>8} {'p95':>8} {'Slow':>5}")
    for b in c.banks:
        lines.append(f"{b.bic:<12} {len(b.runs):>4} {format_rate(b.pass_rate) + '%':>9} {b.passed:>5} {b.failed:>5} {b.errors:>4} "
                     f"{b.requests:>8} {format_ms(b.latency.p50):>8} {format_ms(b.latency.p95):>8} {b.slow:>5}")
    lines += ["", "Latency per operation (p50 / p95, samples)"]
    for op in c.operations:
        cells = [f"{b.bic}: {format_ms(s.p50)} / {format_ms(s.p95)} (n={s.count}{f', {s.errors} errors' if s.errors else ''})"
                 for b in c.banks if (s := c.latency.get((b.bic, op)))]
        lines.append(f"  {op:<52} " + "   ".join(cells))
    lines += ["", "Deviations in the latest run"]
    for b in c.banks:
        for d in b.deviations:
            lines.append(f"  {b.bic:<12} {d.outcome.value:<6} {d.scenario_id:<14} {d.detail}")
            if d.spec_ref:
                lines.append(f"  {'':<12} {'':<6} {'':<14} spec: {d.spec_ref}")
    if not any(b.deviations for b in c.banks):
        lines.append("  none")
    unstable = [(sid, bic, cell) for sid, _, row in c.scenarios for bic, cell in row.items() if not cell.stable]
    lines += ["", "Not reproducible (passed in some runs, not in others)"]
    lines += [f"  {bic:<12} {sid:<14} passed {cell.passed} of {cell.executed}" for sid, bic, cell in unstable] or ["  none"]
    return "\n".join(lines)


# Categorical bank colours in a fixed order (validated light/dark pairs from the dataviz reference palette).
BANK_COLOURS = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"), ("#eda100", "#c98500"),
                ("#e87ba4", "#d55181"), ("#008300", "#008300"), ("#4a3aa7", "#9085e9"), ("#e34948", "#e66767")]


def bank_colour_index(c: Comparison) -> dict[str, int]:
    return {b.bic: i % len(BANK_COLOURS) for i, b in enumerate(c.banks)}


def latency_chart(c: Comparison, colours: dict[str, int] | None = None, width: int = 760) -> str:
    """Inline SVG: one row per operation, one bar per bank for p50 and a thin line on to p95. Hover shows the numbers.
    `colours` maps BIC to a BANK_COLOURS slot, so a bank keeps its colour when the shown banks are filtered."""
    colour = colours or bank_colour_index(c)
    ops = [op for op in c.operations if any((b.bic, op) in c.latency and c.latency[(b.bic, op)].count for b in c.banks)]
    if not ops:
        return ""
    top = max(c.latency[(b.bic, op)].p95 for op in ops for b in c.banks if (b.bic, op) in c.latency and c.latency[(b.bic, op)].count)
    step = _nice_step(max(top, 1) / 4)
    top = step * -(-max(top, 1) // step)  # round the axis up to a whole step
    label_w, bar_h, gap, group_gap = 330, 9, 2, 12
    plot_w = width - label_w - 50
    per_op = [[b for b in c.banks if (b.bic, op) in c.latency and c.latency[(b.bic, op)].count] for op in ops]
    height = sum(len(bs) * (bar_h + gap) + group_gap for bs in per_op) + 24
    out = [f'<svg class="latency-chart" viewBox="0 0 {width} {height}" width="{width}" style="max-width:100%;height:auto" role="img" '
           f'aria-label="p50 and p95 latency per operation and bank">']
    for i in range(int(top // step) + 1):  # grid
        x = label_w + plot_w * i * step / top
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="0" y2="{height - 18}" class="grid"/>'
                   f'<text x="{x:.1f}" y="{height - 4}" class="tick" text-anchor="middle">{i * step} ms</text>')
    y = 0
    for op, bs in zip(ops, per_op):
        block = len(bs) * (bar_h + gap)
        out.append(f'<text x="{label_w - 8}" y="{y + block / 2 + 4:.1f}" class="op" text-anchor="end">{html.escape(op)}</text>')
        for b in bs:
            s = c.latency[(b.bic, op)]
            w50, w95 = max(2.0, plot_w * s.p50 / top), plot_w * s.p95 / top
            tip = f"{b.bic} · {op}: p50 {s.p50} ms, p95 {s.p95} ms, max {s.max} ms, {s.count} samples"
            out.append(f'<g class="bank-{colour[b.bic]}"><title>{html.escape(tip)}</title>'
                       f'<rect x="{label_w - 4}" y="{y - 1}" width="{plot_w + 8}" height="{bar_h + 2}" fill="transparent"/>'
                       f'<line x1="{label_w + w50:.1f}" x2="{label_w + w95:.1f}" y1="{y + bar_h / 2:.1f}" y2="{y + bar_h / 2:.1f}" class="whisker"/>'
                       f'<line x1="{label_w + w95:.1f}" x2="{label_w + w95:.1f}" y1="{y + 1}" y2="{y + bar_h - 1}" class="whisker"/>'
                       f'<rect x="{label_w}" y="{y}" width="{w50:.1f}" height="{bar_h}" rx="2" class="bar"/>'
                       f'<text x="{label_w + w95 + 6:.1f}" y="{y + bar_h - 1}" class="val">{s.p50}</text></g>')
            y += bar_h + gap
        y += group_gap
    out.append("</svg>")
    return "".join(out)


def _nice_step(raw: float) -> int:
    """The smallest 1/2/5 x 10^k at or above raw (at least 1)."""
    magnitude = 1
    while True:
        for factor in (1, 2, 5):
            if factor * magnitude >= raw:
                return factor * magnitude
        magnitude *= 10


def chart_css() -> str:
    """Colours for latency_chart (bank-<i> classes), light and dark."""
    light = "".join(f".latency-chart .bank-{i}{{--bank:{l}}}" for i, (l, _) in enumerate(BANK_COLOURS))
    dark = "".join(f".latency-chart .bank-{i}{{--bank:{d}}}" for i, (_, d) in enumerate(BANK_COLOURS))
    swatch_light = "".join(f".swatch.bank-{i}{{background:{l}}}" for i, (l, _) in enumerate(BANK_COLOURS))
    swatch_dark = "".join(f".swatch.bank-{i}{{background:{d}}}" for i, (_, d) in enumerate(BANK_COLOURS))
    return (light + swatch_light +
            ".latency-chart .bar{fill:var(--bank)}.latency-chart .whisker{stroke:var(--bank);stroke-width:2}"
            ".latency-chart .grid{stroke:var(--line,#e3e4e8);stroke-width:1}"
            ".latency-chart text{font:10.5px system-ui,sans-serif;fill:var(--muted,#6e6e73)}"
            ".latency-chart .op{fill:var(--fg,#1d1d1f);font-family:ui-monospace,Menlo,monospace}"
            ".swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}"
            f"@media (prefers-color-scheme: dark){{{dark}{swatch_dark}}}")
