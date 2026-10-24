"""Trends for the Monitoring page and `aqt monitor trends`, computed in code from the store."""

from dataclasses import dataclass


@dataclass
class Flaky:
    bank_bic: str
    scenario_id: str
    runs: int
    changes: int
    last: str


def flaky(sequences: dict[tuple[str, str], list[tuple]], min_changes: int = 2) -> list[Flaky]:
    """Scenarios whose outcome (Passed vs Failed/Error) changed at least `min_changes` times, most changes first."""
    result = []
    for (bank, scenario), seq in sequences.items():
        states = ["ok" if outcome == "Passed" else "bad" for _, outcome in seq]
        changes = sum(1 for a, b in zip(states, states[1:]) if a != b)
        if changes >= min_changes:
            result.append(Flaky(bank, scenario, len(seq), changes, seq[-1][1]))
    return sorted(result, key=lambda f: (-f.changes, f.bank_bic, f.scenario_id))


def sparkline(values: list[float], width: int = 220, height: int = 36) -> str:
    """An inline SVG polyline of pass rates (0-100), no external libraries."""
    if not values:
        return ""
    if len(values) == 1:
        values = values * 2
    step = width / (len(values) - 1)
    points = " ".join(f"{i * step:.1f},{height - 2 - (v / 100) * (height - 4):.1f}" for i, v in enumerate(values))
    last_x, last_y = points.split(" ")[-1].split(",")
    return (f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="pass rate">'
            f'<line x1="0" y1="2" x2="{width}" y2="2" stroke="currentColor" stroke-opacity=".15"/>'
            f'<polyline fill="none" stroke="currentColor" stroke-width="2" points="{points}"/>'
            f'<circle cx="{last_x}" cy="{last_y}" r="3" fill="currentColor"/></svg>')
