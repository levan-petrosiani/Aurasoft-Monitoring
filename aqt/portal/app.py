"""
The portal web app (Flask). Pages match the C# Blazor portal: Dashboard, New run, Run (live), Runs, Scenarios.
Live progress is polled by the run page; everything else is plain server-rendered HTML with small forms.
"""

import os

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

from ..errors import ConfigurationError
from ..reporting import pretty_json
from ..results import Outcome, format_rate, pass_rate
from ..runner import PREFLIGHT_SUITE
from .services import COMPLETED, CatalogService, MockBankService, RunRequest, RunService

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_REPORT_FILES = {
    "report.html": "text/html",
    "summary.json": "application/json",
    "junit.xml": "application/xml",
    "run.json": "application/json",
}


def badge_css(value) -> str:
    text = str(value)
    if text in ("Passed", "Completed"):
        return "pass"
    if text == "Failed":
        return "fail"
    if text == "Error":
        return "error"
    if text == "Running":
        return "running"
    if text == "Queued":
        return "queued"
    return "muted"


def local_time(dt, fmt="%Y-%m-%d %H:%M:%S") -> str:
    return dt.astimezone().strftime(fmt) if dt else ""


def create_app(root: str | None = None, data_directory: str | None = None, mock_xs2a_url: str = "https://localhost:7443/") -> Flask:
    root = root or os.environ.get("AQT_HOME") or _REPO_ROOT
    data_directory = data_directory or os.path.join(root, "reports", "portal-python")

    app = Flask(__name__)
    catalog = CatalogService(root)
    mock_bank = MockBankService(mock_xs2a_url)
    runs = RunService(catalog, mock_bank, data_directory)
    app.config.update(catalog=catalog, mock_bank=mock_bank, runs=runs)

    app.jinja_env.globals.update(badge_css=badge_css, local_time=local_time, format_rate=format_rate,
                                 Outcome=Outcome, COMPLETED=COMPLETED, pretty_json=pretty_json)

    @app.get("/")
    def home():
        banks = catalog.get_banks()
        all_runs = runs.runs
        last = {}
        for bank in banks:
            last[bank.key] = next((r for r in all_runs if r.record.request.bank.lower() == bank.key.lower()), None)
        return render_template("home.html", page="home", banks=banks, runs=all_runs, last=last,
                               catalog=catalog, mock=mock_bank, mock_reachable=mock_bank.is_reachable(),
                               active=any(r.is_active for r in all_runs))

    @app.post("/mock/start")
    def mock_start():
        try:
            mock_bank.ensure_running()
        except ConfigurationError:
            pass
        return redirect(url_for("home"))

    @app.post("/mock/stop")
    def mock_stop():
        mock_bank.stop()
        return redirect(url_for("home"))

    @app.route("/run/new", methods=["GET", "POST"])
    def new_run():
        try:
            banks = [b for b in catalog.get_banks() if b.profile is not None]
            all_scenarios = catalog.get_scenarios()
        except ConfigurationError as ex:
            return render_template("new_run.html", page="new", load_error=str(ex))

        suites = sorted({s.suite for s in all_scenarios}, key=lambda s: (0 if s == PREFLIGHT_SUITE else 1, s))
        selected_key = request.values.get("bank")
        bank = (next((b for b in banks if selected_key and b.key.lower() == selected_key.lower()), None)
                or next((b for b in banks if b.key == "MOCK"), None)
                or (banks[0] if banks else None))
        is_local = bank is not None and CatalogService.is_local(bank.profile)
        headless = bank is not None and CatalogService.allows_headless_sca(bank.profile)

        error = None
        if request.method == "POST" and bank is not None:
            run_request = RunRequest(
                bank=bank.key,
                suites=request.form.getlist("suite"),
                scenario_ids=request.form.getlist("scenario"),
                use_mock_bank=is_local and request.form.get("use_mock") == "on",
                auto_sca=headless and request.form.get("auto_sca") == "on",
                preflight_gate=request.form.get("preflight_gate") == "on")
            try:
                run = runs.enqueue(run_request)
                return redirect(url_for("run_detail", run_id=run.id))
            except ConfigurationError as ex:
                error = str(ex)

        return render_template("new_run.html", page="new", banks=banks, bank=bank, suites=suites,
                               scenarios=sorted(all_scenarios, key=lambda s: (s.suite, s.id)),
                               is_local=is_local, headless=headless, error=error, form=request.form,
                               posted=request.method == "POST")

    @app.get("/runs")
    def runs_page():
        return render_template("runs.html", page="runs", runs=runs.runs, data_directory=runs.data_directory)

    @app.get("/runs/<run_id>")
    def run_detail(run_id):
        run = runs.get(run_id)
        if run is None:
            return render_template("not_found.html", page="runs"), 404
        completed = run.completed
        filter_ = request.args.get("filter", "All")
        shown = [s for s in completed if filter_ == "All" or s.outcome != Outcome.PASSED]
        template = "run_live.html" if request.args.get("fragment") else "run_detail.html"
        progress = 3 if run.total_scenarios == 0 else max(3, 100 * len(completed) // run.total_scenarios)
        return render_template(template, page="runs", run=run, r=run.record, completed=completed, shown=shown,
                               filter=filter_, rate=format_rate(pass_rate(completed)), progress=progress)

    @app.post("/runs/<run_id>/cancel")
    def run_cancel(run_id):
        runs.cancel(run_id)
        return redirect(url_for("run_detail", run_id=run_id))

    @app.post("/runs/<run_id>/again")
    def run_again(run_id):
        run = runs.get(run_id)
        if run is None:
            abort(404)
        try:
            new = runs.enqueue(run.record.request)
        except ConfigurationError:
            return redirect(url_for("run_detail", run_id=run_id))
        return redirect(url_for("run_detail", run_id=new.id))

    @app.post("/runs/<run_id>/delete")
    def run_delete(run_id):
        runs.delete(run_id)
        return redirect(url_for("runs_page"))

    @app.get("/runs/<run_id>/files/<file>")
    def run_file(run_id, file):
        """Report files of a run (report.html, summary.json, junit.xml)."""
        if file not in _REPORT_FILES or runs.get(run_id) is None:
            abort(404)
        path = os.path.join(runs.run_directory(run_id), file)
        if not os.path.isfile(path):
            abort(404)
        return send_file(path, mimetype=_REPORT_FILES[file])

    @app.get("/scenarios")
    def scenarios_page():
        error, groups = None, []
        try:
            loaded = catalog.get_scenarios()
            names = sorted({s.suite for s in loaded}, key=lambda s: (0 if s == PREFLIGHT_SUITE else 1, s))
            groups = [(n, sorted((s for s in loaded if s.suite == n), key=lambda s: s.id)) for n in names]
        except ConfigurationError as ex:
            error = str(ex)
        return render_template("scenarios.html", page="scenarios", groups=groups, error=error,
                               directory=catalog.scenarios_directory)

    @app.errorhandler(500)
    def server_error(_):
        return render_template("error.html", page=""), 500

    return app


def run_portal(cli) -> int:
    """aqt portal [--port 5280] [--root dir] [--data dir]"""
    port = int(cli.get("port") or 5280)
    app = create_app(cli.get("root"), cli.get("data"))
    runs: RunService = app.config["runs"]
    print(f"API Test Portal (Python): http://localhost:{port}/")
    print(f"  banks/scenarios from {app.config['catalog'].root_directory}")
    print(f"  runs stored in {runs.data_directory}")
    print("  Press Ctrl+C to stop.", flush=True)
    try:
        # Local tool: listens on the loopback interface only and has no login of its own.
        app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)
    finally:
        app.config["mock_bank"].stop()
    return 0
