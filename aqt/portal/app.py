"""
The portal web app (Flask). Pages match the C# Blazor portal: Dashboard, New run, Run (live), Runs, Scenarios.
Live progress is polled by the run page; everything else is plain server-rendered HTML with small forms.
"""

import os

from types import SimpleNamespace
from urllib.parse import urlencode, urlsplit

from flask import Flask, abort, make_response, redirect, render_template, request, send_file, url_for

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


_REVIEWER_COOKIE = "aqt_reviewer"


def _local_path(url: str | None, default: str) -> str:
    """Only redirect back to a path on this portal."""
    if not url:
        return default
    parts = urlsplit(url)
    return url if not parts.scheme and not parts.netloc and url.startswith("/") else default


def create_app(root: str | None = None, data_directory: str | None = None, mock_xs2a_url: str = "https://localhost:7443/",
               database: str | None = None, use_database: bool = True, auto_summarize: bool = False,
               monitor: bool = False) -> Flask:
    root = root or os.environ.get("AQT_HOME") or _REPO_ROOT
    data_directory = data_directory or os.path.join(root, "reports", "portal-python")

    app = Flask(__name__)
    catalog = CatalogService(root)
    mock_bank = MockBankService(mock_xs2a_url)

    # Store and AI layer (optional): runs are also written to PostgreSQL; AI summary, Standards and Rules pages.
    ai, ai_problem = None, "The store and AI pages are off (the portal was started with --no-db)."
    if use_database:
        from .ai_services import try_create
        recorder, summaries, rules, drafts, asking, monitoring, ai_problem = try_create(database or os.environ.get("AQT_DB"), root, auto_summarize)
        if recorder is not None:
            ai = SimpleNamespace(recorder=recorder, summaries=summaries, rules=rules, drafts=drafts, ask=asking, monitor=monitoring,
                                 store=recorder.store)
    runs = RunService(catalog, mock_bank, data_directory, ai.recorder if ai else None, ai.summaries if ai else None)
    if ai is not None:
        ai.monitor.runs = runs
        if monitor:
            ai.monitor.start(runs)
    app.config.update(catalog=catalog, mock_bank=mock_bank, runs=runs, ai=ai, ai_problem=ai_problem)

    @app.context_processor
    def ai_context():
        return {"ai": ai, "ai_problem": ai_problem, "reviewer": request.cookies.get(_REVIEWER_COOKIE, "")}

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
        summary_row = summary = None
        if ai is not None and not run.is_active:
            from ..ai.summaries import RunSummary
            summary_row = ai.summaries.latest(run_id)
            summary = RunSummary.from_json(summary_row.summary_json) if summary_row and summary_row.summary_json else None
        return render_template(template, page="runs", run=run, r=run.record, completed=completed, shown=shown,
                               filter=filter_, rate=format_rate(pass_rate(completed)), progress=progress,
                               summary_row=summary_row, summary=summary)

    @app.post("/runs/<run_id>/summarize")
    def run_summarize(run_id):
        if ai is None or runs.get(run_id) is None:
            abort(404)
        provider = request.form.get("provider", "anthropic")
        if provider not in ("anthropic", "ollama"):
            abort(400)
        ai.summaries.start(run_id, provider, f"{request.cookies.get(_REVIEWER_COOKIE) or 'someone'} (portal)")
        return redirect(url_for("run_detail", run_id=run_id) + "#ai-summary")

    # ---- reviewer, standards, rules (store + AI layer) ----------------------------------------------------------

    @app.post("/reviewer")
    def set_reviewer():
        response = make_response(redirect(_local_path(request.form.get("next"), "/rules")))
        name = (request.form.get("name") or "").strip()
        if name:
            response.set_cookie(_REVIEWER_COOKIE, name, max_age=365 * 24 * 3600, samesite="Lax")
        else:
            response.delete_cookie(_REVIEWER_COOKIE)
        return response

    def _errors():
        return {request.args.get("rule"): request.args.get("error")} if request.args.get("error") else {}

    @app.get("/standards")
    def standards_page():
        context = {"page": "standards", "docs": [], "doc": None, "outline": [], "rule_counts": {}, "clause": None,
                   "ancestors": [], "clause_rules": [], "hits": None, "q": request.args.get("q"), "drafting": None,
                   "last": None, "errors": _errors(), "edit": request.args.get("edit")}
        if ai is None:
            return render_template("standards.html", **context)
        store = ai.store
        docs = store.list_standards()
        key = request.args.get("clause")
        wanted = request.args.get("doc") or (key.rsplit(":", 1)[0] if key else None)
        doc = next((d for d in docs if wanted == f"{d.id}:{d.version}"), docs[0] if docs else None)
        counts = {}
        for r in store.list_rules():
            if r.status in ("draft", "approved"):
                counts[r.clause_key] = counts.get(r.clause_key, 0) + 1
        context.update(docs=docs, doc=doc, rule_counts=counts, outline=store.get_outline(doc.id, doc.version) if doc else [])
        if context["q"]:
            context["hits"] = ai.rules.search(context["q"])
        if key:
            clause = store.get_clause(key)
            if clause:
                context.update(clause=clause, ancestors=store.get_ancestors(clause), clause_rules=store.list_rules(clause_key=key),
                               drafting=ai.rules.drafting_provider(key), last=ai.rules.last_draft(key))
        return render_template("standards.html", **context)

    @app.post("/standards/draft")
    def standards_draft():
        if ai is None:
            abort(404)
        key, provider = request.form.get("clause", ""), request.form.get("provider", "anthropic")
        reviewer = request.cookies.get(_REVIEWER_COOKIE)
        if provider in ("anthropic", "ollama") and reviewer and ai.store.get_clause(key):
            ai.rules.start_draft(key, provider, f"{reviewer} (portal)")
        return redirect("/standards?" + urlencode({"clause": key}) + "#std-main")

    @app.get("/rules")
    def rules_page():
        status, standard = request.args.get("status", "draft"), request.args.get("standard", "")
        context = {"page": "rules", "shown": [], "counts": {}, "standards": [], "status": status, "standard": standard,
                   "message": request.args.get("message"), "errors": _errors(), "edit": request.args.get("edit"),
                   "rules_directory": ""}
        if ai is None:
            return render_template("rules.html", **context)
        every = ai.store.list_rules()
        counts = {s: sum(r.status == s for r in every) for s in ("draft", "approved", "rejected", "invalid", "retired")}
        counts["all"] = len(every)
        context.update(counts=counts, standards=sorted({r.standard for r in every}), rules_directory=ai.rules.rules_directory,
                       shown=[r for r in every if (status == "all" or r.status == status) and (not standard or r.standard == standard)])
        return render_template("rules.html", **context)

    def _review(rule_id: str, action, back: str):
        reviewer = request.cookies.get(_REVIEWER_COOKIE)
        error = "Enter your name at the top to review." if not reviewer else ai.rules.review(lambda book: action(book, reviewer))
        back = back.split("#")[0]
        # Back to the list without the edit/error state of the previous action.
        base, _, query = back.partition("?")
        kept = [p for p in query.split("&") if p and p.split("=")[0] not in ("edit", "rule", "error")]
        if error:
            kept.append(urlencode({"rule": rule_id, "error": error}))
        return redirect(base + ("?" + "&".join(kept) if kept else "") + f"#rule-{rule_id}")

    def _back():
        return _local_path(request.form.get("back"), "/rules")

    @app.post("/rules/<rule_id>/approve")
    def rule_approve(rule_id):
        if ai is None:
            abort(404)
        return _review(rule_id, lambda book, who: book.approve(rule_id, who), _back())

    @app.post("/rules/<rule_id>/reject")
    def rule_reject(rule_id):
        if ai is None:
            abort(404)
        return _review(rule_id, lambda book, who: book.reject(rule_id, who, request.form.get("note", "")), _back())

    @app.post("/rules/<rule_id>/reopen")
    def rule_reopen(rule_id):
        if ai is None:
            abort(404)
        return _review(rule_id, lambda book, who: book.reopen(rule_id, who), _back())

    @app.post("/rules/<rule_id>/edit")
    def rule_edit(rule_id):
        if ai is None:
            abort(404)
        from ..ai.rules import RuleEdit
        rule = ai.store.get_rule(rule_id)
        if rule is None:
            abort(404)
        f = request.form

        def changed(name, current):
            value = f.get(name)
            return None if value is None or value.strip() == (current or "").strip() else value.strip()

        check = dict(rule.check_json or {})
        statuses = [int(x) for x in (f.get("expect_status") or "").replace(" ", "").split(",") if x.isdigit()]
        codes = [x.strip() for x in (f.get("expect_codes") or "").split(",") if x.strip()]
        check_changed = statuses != check.get("expectStatus", []) or codes != check.get("expectErrorCodes", [])
        check.update(expectStatus=statuses, expectErrorCodes=codes)
        edit = RuleEdit(title=changed("title", rule.title), level=changed("level", rule.level),
                        service=changed("service", rule.service), area=changed("area", rule.area),
                        requirement=changed("requirement", rule.requirement), quote=changed("quote", rule.quote),
                        quote_english=changed("quote_english", rule.quote_english), operation=changed("operation", rule.operation),
                        check_json=check if check_changed else None)
        return _review(rule_id, lambda book, who: book.edit(rule_id, who, edit), _back())

    @app.post("/rules/export")
    def rules_export():
        if ai is None:
            abort(404)
        written, removed = ai.rules.export()
        return redirect("/rules?" + urlencode({"message": f"{written} approved rule(s) written to {ai.rules.rules_directory}, "
                                                          f"{removed} stale file(s) removed. Commit them to keep the history in git."}))


    # ---- scenario drafts and coverage (Phase 3) -------------------------------------------------------------------

    @app.get("/drafts")
    def drafts_page():
        status, rule_id = request.args.get("status", "draft"), request.args.get("rule", "")
        context = {"page": "drafts", "shown": [], "counts": {}, "status": status, "rule_id": rule_id, "approved_rules": [],
                   "busy": {}, "failed_jobs": {}, "errors": {request.args.get("draft"): request.args.get("error")} if request.args.get("error") else {},
                   "edit": request.args.get("edit"), "message": request.args.get("message")}
        if ai is None:
            return render_template("drafts.html", **context)
        every = ai.store.list_scenario_drafts()
        counts = {s: sum(d.status == s for d in every) for s in ("draft", "failed", "approved", "rejected")}
        counts["all"] = len(every)
        approved = [r for r in ai.store.list_rules("approved")]
        context.update(counts=counts, approved_rules=approved,
                       busy={r.id: p for r in approved if (p := ai.drafts.drafting_provider(r.id))},
                       failed_jobs={r.id: e for r in approved if (e := ai.drafts.last_error(r.id))},
                       models={p: ai.drafts.model_for(p) for p in ("anthropic", "ollama")},
                       shown=[d for d in every if (status == "all" or d.status == status) and (not rule_id or d.rule_id == rule_id)])
        return render_template("drafts.html", **context)

    @app.post("/drafts/new")
    def drafts_new():
        if ai is None:
            abort(404)
        rule_id, provider = request.form.get("rule", ""), request.form.get("provider", "anthropic")
        reviewer = request.cookies.get(_REVIEWER_COOKIE)
        rule = ai.store.get_rule(rule_id)
        if not reviewer:
            return redirect("/drafts?" + urlencode({"rule": rule_id, "message": "Enter your name at the top first."}))
        if provider in ("anthropic", "ollama") and rule is not None and rule.status == "approved":
            ai.drafts.start_draft(rule_id, provider, f"{reviewer} (portal)")
        return redirect("/drafts?" + urlencode({"rule": rule_id, "status": "all"}))

    def _draft_review(draft_id: str, action):
        reviewer = request.cookies.get(_REVIEWER_COOKIE)
        error = "Enter your name at the top to review." if not reviewer else ai.drafts.review(lambda review: action(review, reviewer))
        base, _, query = _local_path(request.form.get("back"), "/drafts").split("#")[0].partition("?")
        kept = [p for p in query.split("&") if p and p.split("=")[0] not in ("edit", "draft", "error", "message")]
        if error:
            kept.append(urlencode({"draft": draft_id, "error": error}))
        return redirect(base + ("?" + "&".join(kept) if kept else "") + f"#draft-{draft_id}")

    @app.post("/drafts/<draft_id>/approve")
    def draft_approve(draft_id):
        if ai is None:
            abort(404)
        return _draft_review(draft_id, lambda review, who: review.approve(draft_id, who, request.form.get("note") or None))

    @app.post("/drafts/<draft_id>/reject")
    def draft_reject(draft_id):
        if ai is None:
            abort(404)
        return _draft_review(draft_id, lambda review, who: review.reject(draft_id, who, request.form.get("note", "")))

    @app.post("/drafts/<draft_id>/edit")
    def draft_edit(draft_id):
        if ai is None:
            abort(404)
        return _draft_review(draft_id, lambda review, who: review.edit(draft_id, who, request.form.get("yaml", "")))

    @app.get("/coverage")
    def coverage_page():
        context = {"page": "coverage", "coverage": None, "include_drafts": request.args.get("drafts") == "1"}
        if ai is not None:
            context["coverage"] = ai.drafts.coverage(context["include_drafts"])
        return render_template("coverage.html", **context)

    # ---- ask-agent (Phase 4) ---------------------------------------------------------------------------------

    @app.get("/ask")
    def ask_page():
        context = {"page": "ask", "questions": [], "pending": [], "errors": {}, "models": {}, "message": request.args.get("message"),
                   "open_id": request.args.get("q")}
        if ai is not None:
            context.update(questions=ai.store.list_agent_questions(30), pending=ai.ask.pending(), errors=ai.ask.errors(),
                           models={p: ai.ask.model_for(p) for p in ("anthropic", "ollama")})
        return render_template("ask.html", **context)

    @app.post("/ask")
    def ask_post():
        if ai is None:
            abort(404)
        question, provider = (request.form.get("question") or "").strip(), request.form.get("provider", "anthropic")
        reviewer = request.cookies.get(_REVIEWER_COOKIE)
        if not reviewer:
            return redirect("/ask?" + urlencode({"message": "Enter your name at the top first (it is saved with the question)."}))
        if question and provider in ("anthropic", "ollama"):
            ai.ask.start(question[:2000], provider, f"{reviewer} (portal)")
        return redirect("/ask")

    # ---- monitoring and alerts (Phase 6) -----------------------------------------------------------------------

    @app.get("/monitoring")
    def monitoring_page():
        from ..monitor import trends
        context = {"page": "monitoring", "schedules": [], "retention": None, "series": {}, "flaky": [], "slow": [],
                   "problem": None, "enabled": False, "message": request.args.get("message"), "sparkline": trends.sparkline,
                   "open_alerts": 0}
        if ai is not None:
            schedules, retention = ai.monitor.overview()
            context.update(schedules=schedules, retention=retention, series=ai.store.pass_rate_series(30),
                           flaky=trends.flaky(ai.store.outcome_sequences(30))[:20], slow=ai.store.slowest_endpoints(30, 10),
                           problem=ai.monitor.problem, enabled=ai.monitor.enabled,
                           open_alerts=len(ai.store.list_alerts(open_only=True)))
        return render_template("monitoring.html", **context)

    @app.post("/monitoring/run/<name>")
    def monitoring_run_now(name):
        if ai is None:
            abort(404)
        who = request.cookies.get(_REVIEWER_COOKIE) or "portal"
        error = ai.monitor.run_now(name, f"{who} (Run now)")
        return redirect("/monitoring" + ("?" + urlencode({"message": error}) if error else ""))

    @app.get("/alerts")
    def alerts_page():
        show = request.args.get("show", "open")
        context = {"page": "alerts", "alerts": [], "show": show, "open_count": 0}
        if ai is not None:
            context.update(alerts=ai.store.list_alerts(open_only=show == "open"), open_count=len(ai.store.list_alerts(open_only=True)))
        return render_template("alerts.html", **context)

    @app.post("/alerts/ack")
    def alerts_ack():
        if ai is None:
            abort(404)
        who = request.cookies.get(_REVIEWER_COOKIE)
        if not who:
            return redirect("/alerts?" + urlencode({"show": "open", "error": "Enter your name at the top first."}))
        ids = request.form.getlist("id")
        if ids:
            ai.store.acknowledge_alerts(ids, who)
        return redirect("/alerts")

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
    """aqt portal [--port 5280] [--root dir] [--data dir] [--db connection | --no-db] [--auto-summarize] [--no-monitor]"""
    port = int(cli.get("port") or 5280)
    app = create_app(cli.get("root"), cli.get("data"), database=cli.get("db"), use_database=not cli.has("no-db"),
                     auto_summarize=cli.has("auto-summarize"), monitor=not cli.has("no-monitor"))
    runs: RunService = app.config["runs"]
    print(f"API Test Portal (Python): http://localhost:{port}/")
    print(f"  banks/scenarios from {app.config['catalog'].root_directory}")
    print(f"  runs stored in {runs.data_directory}")
    print(f"  store and AI pages: {'on' if app.config['ai'] else 'off - ' + app.config['ai_problem']}")
    print("  Press Ctrl+C to stop.", flush=True)
    try:
        # Local tool: listens on the loopback interface only and has no login of its own.
        app.run(host="127.0.0.1", port=port, threaded=True, debug=False, use_reloader=False)
    finally:
        app.config["mock_bank"].stop()
    return 0
