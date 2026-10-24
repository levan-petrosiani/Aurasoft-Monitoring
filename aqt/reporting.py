"""Report files of a run: report.html, summary.json, junit.xml (same content as the C# tool)."""

import html
import json
import os
import xml.etree.ElementTree as ET

from .errors import ConfigurationError
from .results import Outcome, RunResult, StepResult, format_rate

SUPPORTED_FORMATS = ["html", "json", "junit"]


def write(run: RunResult, directory: str, formats) -> list[str]:
    """Writes the requested report formats into directory and returns the file paths."""
    os.makedirs(directory, exist_ok=True)
    files = []
    seen = set()

    for fmt in (f.strip().lower() for f in formats):
        if fmt in seen:
            continue
        seen.add(fmt)

        if fmt == "json":
            name, content = "summary.json", summary_json(run)
        elif fmt == "html":
            name, content = "report.html", html_report(run)
        elif fmt == "junit":
            name, content = "junit.xml", junit(run)
        else:
            raise ConfigurationError(f"Unknown report format '{fmt}'. Supported: {', '.join(SUPPORTED_FORMATS)}")

        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        files.append(path)

    return files


def summary_json(run: RunResult) -> str:
    return json.dumps(run.to_dict(), indent=2, ensure_ascii=False)


def junit(run: RunResult) -> str:
    suites = ET.Element("testsuites", {
        "name": f"ApiTestTool {run.bank_bic}",
        "tests": str(len(run.scenarios)),
        "failures": str(run.count(Outcome.FAILED)),
        "errors": str(run.count(Outcome.ERROR)),
        "skipped": str(run.count(Outcome.SKIPPED)),
    })

    groups: dict[str, list] = {}
    for s in run.scenarios:
        groups.setdefault(s.suite, []).append(s)

    for suite_name, group in groups.items():
        suite = ET.SubElement(suites, "testsuite", {
            "name": f"{run.bank_bic}.{suite_name}",
            "tests": str(len(group)),
            "failures": str(sum(s.outcome == Outcome.FAILED for s in group)),
            "errors": str(sum(s.outcome == Outcome.ERROR for s in group)),
            "skipped": str(sum(s.outcome == Outcome.SKIPPED for s in group)),
            "timestamp": run.started_at_utc.strftime("%Y-%m-%dT%H:%M:%S") if run.started_at_utc else "",
        })

        for s in group:
            case = ET.SubElement(suite, "testcase", {
                "classname": f"{run.bank_bic}.{s.suite}",
                "name": f"{s.id} {s.title}",
                "time": f"{s.duration_ms / 1000:.3f}",
            })

            details = "\n".join(f"[{st.name}] {f}" for st in s.steps for f in st.failures)
            if s.outcome == Outcome.FAILED:
                ET.SubElement(case, "failure", {"message": s.reason or "failed"}).text = details
            elif s.outcome == Outcome.ERROR:
                ET.SubElement(case, "error", {"message": s.reason or "error"}).text = details
            elif s.outcome == Outcome.SKIPPED:
                ET.SubElement(case, "skipped", {"message": s.reason or "skipped"})

            warnings = s.all_warnings
            if warnings:
                ET.SubElement(case, "system-out").text = "\n".join("WARNING: " + w for w in warnings)

    ET.indent(suites, space="  ")
    return ET.tostring(suites, encoding="unicode")


def _e(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


_STYLE = """
  :root { --bg:#fff; --fg:#1d1d1f; --muted:#6e6e73; --line:#e5e5ea; --pass:#1a7f37; --fail:#cf222e; --warn:#9a6700; --skip:#6e6e73; --err:#8250df; --code:#f6f8fa; }
  @media (prefers-color-scheme: dark) { :root { --bg:#111; --fg:#e8e8ea; --muted:#9a9aa0; --line:#2c2c30; --pass:#3fb950; --fail:#f85149; --warn:#d29922; --skip:#8b949e; --err:#a371f7; --code:#1b1b1f; } }
  body { font: 14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; background: var(--bg); color: var(--fg); margin: 0 auto; max-width: 1100px; padding: 24px 16px; }
  h1 { font-size: 22px; margin: 0 0 4px; } .muted { color: var(--muted); }
  .tiles { display: flex; flex-wrap: wrap; gap: 12px; margin: 20px 0; }
  .tile { border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px; min-width: 110px; }
  .tile b { display: block; font-size: 22px; }
  table { width: 100%; border-collapse: collapse; } th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
  .o-Passed { color: var(--pass); } .o-Failed { color: var(--fail); } .o-Blocked, .o-Skipped { color: var(--skip); } .o-Error { color: var(--err); }
  details { border: 1px solid var(--line); border-radius: 8px; margin: 10px 0; padding: 8px 12px; } summary { cursor: pointer; font-weight: 600; }
  pre { background: var(--code); padding: 8px; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
  ul { margin: 4px 0; padding-left: 20px; } .warn { color: var(--warn); } .fail { color: var(--fail); }
"""


def html_report(run: RunResult) -> str:
    out = []
    started = run.started_at_utc.strftime("%Y-%m-%d %H:%M:%S") if run.started_at_utc else ""
    out.append(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>API test report — {_e(run.bank_bic)}</title>
<style>{_STYLE}</style></head><body>
<h1>{_e(run.bank_name)} ({_e(run.bank_bic)})</h1>
<div class="muted">{_e(run.xs2a_url)} · TPP {_e(run.tpp_id or "—")} · {started} UTC · ApiTestTool {_e(run.tool_version)} (Python)</div>
<div class="tiles">
  <div class="tile"><b>{format_rate(run.pass_rate)}%</b>pass rate</div>
  <div class="tile"><b class="o-Passed">{run.count(Outcome.PASSED)}</b>passed</div>
  <div class="tile"><b class="o-Failed">{run.count(Outcome.FAILED)}</b>failed</div>
  <div class="tile"><b class="o-Error">{run.count(Outcome.ERROR)}</b>errors</div>
  <div class="tile"><b class="o-Skipped">{run.count(Outcome.SKIPPED)}</b>skipped</div>
</div>
<table><thead><tr><th>ID</th><th>Scenario</th><th>Suite</th><th>Severity</th><th>Result</th><th>Reason</th></tr></thead><tbody>
""")

    for s in run.scenarios:
        out.append(f'<tr><td><a href="#{_e(s.id)}">{_e(s.id)}</a></td><td>{_e(s.title)}</td><td>{_e(s.suite)}</td>'
                   f'<td>{_e(s.severity)}</td><td class="o-{s.outcome}">{s.outcome}</td><td>{_e(s.reason or "")}</td></tr>')

    out.append("</tbody></table><h2>Details</h2>")

    for s in run.scenarios:
        is_open = " open" if s.outcome in (Outcome.FAILED, Outcome.ERROR) else ""
        out.append(f'<details id="{_e(s.id)}"{is_open}><summary><span class="o-{s.outcome}">{s.outcome}</span> · '
                   f'{_e(s.id)} — {_e(s.title)}</summary>')
        if s.spec_ref is not None:
            out.append(f'<div class="muted">Spec: {_e(s.spec_ref)}</div>')
        if s.origin is not None:
            out.append(f'<div class="muted">Origin: {_e(s.origin)}</div>')

        for step in s.steps:
            _append_step(out, step, False)
        for step in s.cleanup:
            _append_step(out, step, True)

        out.append("</details>")

    out.append("</body></html>")
    return "".join(out)


def _append_step(out: list[str], step: StepResult, is_cleanup: bool):
    out.append(f'<h4>{"Cleanup: " if is_cleanup else ""}{_e(step.name)} <span class="o-{step.outcome}">{step.outcome}</span> '
               f'<span class="muted">{step.duration_ms} ms</span></h4>')
    _append_list(out, step.failures, "fail")
    _append_list(out, step.warnings, "warn")
    _append_list(out, step.info, "muted")

    for x in step.all_exchanges():
        request = f"{x.method} {x.url}\n" + "".join(f"{k}: {v}\n" for k, v in x.request_headers)
        if x.request_body is not None:
            request += "\n" + x.request_body

        response = f"(no response) {x.error}\n" if x.status_code is None else f"{x.status_code} ({x.duration_ms} ms)\n"
        response += "".join(f"{k}: {v}\n" for k, v in x.response_headers)
        if x.response_body:
            response += "\n" + pretty_json(x.response_body)

        out.append(f'<div class="muted">Request</div><pre>{_e(request)}</pre><div class="muted">Response</div><pre>{_e(response)}</pre>')


def _append_list(out: list[str], items: list[str], css: str):
    if not items:
        return
    out.append(f'<ul class="{css}">' + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>")


def pretty_json(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except ValueError:
        return text
