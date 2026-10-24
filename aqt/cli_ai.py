"""
aqt db | ai | standards | rules | scenarios | coverage | ask — the store and AI commands, and the store hook of `aqt run`.

Imports of the database and AI packages happen inside the commands, so the core tool (run, mock, portal, validate)
works without the `ai` extra installed.
"""

import json
import os

from .errors import ConfigurationError

SUCCESS, TESTS_FAILED, CONFIGURATION_ERROR = 0, 1, 2

AI_FLAGS = {"replace", "show-input", "no-embed"}

USAGE = """  aqt db migrate|status [--db <connection>]           create/upgrade the PostgreSQL store, show row counts
  aqt db import [folder...] [--replace]               load report folders (default: ./reports) into the store
  aqt ai check [--provider anthropic|ollama] [--model name]   one-line call to each AI provider, audited
  aqt ai summarize <run-id|latest> [--bank BIC] [--provider ..] [--model ..] [--show-input]   AI triage of a stored run
  aqt standards fetch|ingest [--standard ID] [--no-embed] | list | search "<text>" [--standard ID] | show <clause-key>
  aqt rules draft <clause-key> [--provider ..] [--model ..] | list [--status s] [--standard ID] | show <id>
  aqt rules approve|reject|reopen <id> --by <name> [--note ..] | export        rules from the standards
  aqt scenarios draft <rule-id> [--provider ..] [--model ..] | drafts [--status s] [--rule id] | show <draft>
  aqt scenarios approve|reject <draft> --by <name> [--note ..]   AI scenario drafts (checked on MockBank only)
  aqt coverage [--drafts]                             approved rules -> scenarios -> last result per bank
  aqt ask "<question>" [--provider ..] [--model ..] | ask history     questions about runs, results, rules, standards
    (store commands use --db or AQT_DB; db/ai/standards/rules default to the docker-compose database, localhost:5435)"""


def _out():
    from .cli import Out
    return Out


def run_connection(cli) -> str | None:
    """`aqt run` saves to the store only when --db or AQT_DB is set."""
    from .store import from_environment
    return cli.get("db") or from_environment()


def _connection(cli) -> str:
    from .store import DOCKER_COMPOSE_DEFAULT
    connection = run_connection(cli)
    if connection is None:
        _out().muted("No --db or AQT_DB set; using the docker-compose database (localhost:5435).")
        return DOCKER_COMPOSE_DEFAULT
    return connection


def _store(cli):
    try:
        from .store import AqtStore
    except ImportError as ex:
        raise ConfigurationError(f"The store needs the AI extra: pip install -e '.[ai]' ({ex})") from ex
    return AqtStore(_connection(cli))


def _database_problem(ex: Exception) -> bool:
    try:
        import psycopg
    except ImportError:
        return False
    return isinstance(ex, (psycopg.Error, OSError))


def save_run(connection: str, run_id: str, report_directory: str, run, suites: list[str]):
    """Writes a finished CLI run to the store. A database problem never fails the test run: the reports on disk are
    complete, and the folder can be imported later with `aqt db import`."""
    Out = _out()
    try:
        from .results import Outcome
        from .store import AqtStore, RunInfo
        store = AqtStore(connection)
        store.save_run(RunInfo(id=run_id, source="cli", status="Completed", bank_bic=run.bank_bic, bank_name=run.bank_name,
                               created_utc=run.started_at_utc, started_utc=run.started_at_utc, finished_utc=run.finished_at_utc,
                               suites=list(suites), report_directory=os.path.abspath(report_directory)), run)
        problems = run.count(Outcome.FAILED) + run.count(Outcome.ERROR)
        store.add_event("cli", "run.finished", f"{run.bank_bic}: {run.count(Outcome.PASSED)} passed, {run.count(Outcome.FAILED)} failed, "
                        f"{run.count(Outcome.ERROR)} errors", run_id, "Warning" if problems else "Info")
        Out.muted(f"  saved to the store as {run_id}")
    except Exception as ex:  # noqa: BLE001 - any store problem is only a notice for a test run
        Out.notice(f"Could not save the run to the store ({ex}); import it later with 'aqt db import'.")


# ---- aqt db --------------------------------------------------------------------------------------------------

def cmd_db(cli) -> int:
    Out = _out()
    sub = cli.positional[0] if cli.positional else "status"
    store = _store(cli)
    try:
        if sub == "migrate":
            applied = store.migrate()
            Out.ok(f"Database schema is up to date{' (applied ' + ', '.join(applied) + ')' if applied else ''}")
            return SUCCESS
        if sub == "import":
            from .store.importer import import_folders
            roots = cli.positional[1:] or [os.path.join(os.getcwd(), "reports")]
            result = import_folders(store, roots, cli.has("replace"), lambda line: Out.ok("imported  " + line))
            for problem in result.problems:
                Out.error(problem)
            Out.muted(f"{result.imported} imported, {result.skipped} already in the store (use --replace to overwrite), {len(result.problems)} problem(s)")
            return SUCCESS if not result.problems else CONFIGURATION_ERROR
        if sub == "status":
            status = store.status()
            if status.pending_migrations:
                Out.notice(f"Pending migrations: {', '.join(status.pending_migrations)} - run 'aqt db migrate'")
            print(f"runs {status.runs} · scenario results {status.scenarios} · exchanges {status.exchanges} · events {status.events} · AI calls {status.ai_calls}")
            return SUCCESS
    except Exception as ex:  # noqa: BLE001
        if _database_problem(ex):
            Out.error(f"Database: {ex}. Is it running? (docker compose up -d)")
            return CONFIGURATION_ERROR
        raise
    raise ConfigurationError("Usage: aqt db migrate|import [folder...] [--replace]|status [--db <connection>]")


# ---- aqt ai --------------------------------------------------------------------------------------------------

def _factory(store, cli, feature: str):
    from .ai import options as opt
    from .ai.chat import ChatFactory, InMemoryAuditSink, StoreAuditSink
    options = opt.AiOptions.from_environment()
    if cli.get("provider") or cli.get("model"):
        current = options.for_feature(feature)
        provider = cli.get("provider") or current.provider
        options = options.with_provider(feature, provider)
        if cli.get("model"):
            options.features[feature].model = cli.get("model")
    audit = StoreAuditSink(store, _out().notice) if store is not None else InMemoryAuditSink()
    return ChatFactory(options, audit, store.masker if store is not None else None) if store is not None else ChatFactory(options, audit)


def cmd_ai(cli) -> int:
    sub = cli.positional[0] if cli.positional else ""
    if sub == "check":
        return _ai_check(cli)
    if sub == "summarize":
        return _ai_summarize(cli)
    raise ConfigurationError("Usage: aqt ai check [--provider anthropic|ollama] [--model name] [--db <connection>]\n"
                             "       aqt ai summarize <run-id|latest> [--bank BIC] [--provider ..] [--model ..] [--show-input]")


def _ai_check(cli) -> int:
    """Sends a one-line prompt to each provider to prove the key / local server and the audit log work."""
    from .ai import options as opt
    from .ai.chat import ProviderError
    Out = _out()
    store = _store(cli) if run_connection(cli) else None
    providers = [cli.get("provider")] if cli.get("provider") else [opt.ANTHROPIC, opt.OLLAMA]
    ok = True
    for provider in providers:
        factory = _factory(store, cli, opt.CHECK)
        factory.options = factory.options.with_provider(opt.CHECK, provider)
        if cli.get("model"):
            factory.options.features[opt.CHECK].model = cli.get("model")
        settings = factory.describe(opt.CHECK)
        try:
            import time
            started = time.monotonic()
            result = factory.create(opt.CHECK).complete("You check that the connection works.", [{"role": "user", "content": "Reply with exactly: OK"}])
            Out.ok(f"{provider:<9} {settings.model:<18} {int((time.monotonic() - started) * 1000)} ms  in {result.input_tokens} / out {result.output_tokens} tokens  → {result.text.strip()[:40]}")
        except Exception as ex:  # noqa: BLE001 - every provider problem is reported, the next provider is still tried
            ok = False
            hint = ex
            if provider == opt.ANTHROPIC and ("401" in str(ex) or "auth" in str(ex).lower() or "api_key" in str(ex).lower()):
                hint = f"no valid key: set ANTHROPIC_API_KEY (or run 'ant auth login'). {ex}"
            Out.error(f"{provider:<9} {settings.model:<18} {hint}")
    Out.muted("Audit records written to " + ("the ai_calls table." if store else "memory only (set --db or AQT_DB to keep them)."))
    return SUCCESS if ok else CONFIGURATION_ERROR


def _ai_summarize(cli) -> int:
    from .ai import options as opt
    from .ai.summaries import RunSummarizer, build_digest
    Out = _out()
    store = _store(cli)
    target = cli.positional[1] if len(cli.positional) > 1 else "latest"
    run_id = store.get_latest_run_id(cli.get("bank")) if target.lower() == "latest" else target
    if run_id is None:
        raise ConfigurationError("The store has no runs yet (aqt db import, or run with --db)")

    factory = _factory(store, cli, opt.SUMMARY)
    settings = factory.describe(opt.SUMMARY)
    if cli.has("show-input"):
        run = store.get_run(run_id)
        if run is None:
            raise ConfigurationError(f"Run '{run_id}' is not in the store")
        Out.muted("--- input sent to the model ---")
        print(build_digest(run, store.get_previous_run(run)).text)
        Out.muted("--- end of input ---")

    Out.muted(f"Summarizing {run_id} with {settings.provider} {settings.model} ...")
    try:
        result = RunSummarizer(store, factory).summarize(run_id, f"{os.environ.get('USER', 'cli')} (aqt ai summarize)")
    except ValueError as ex:
        raise ConfigurationError(str(ex)) from ex
    row = result.row
    print()
    if result.summary is not None:
        print(result.summary.to_text())
    else:
        Out.error(f"No summary ({row.status}): {row.text}")
    for note in row.validation_notes:
        Out.notice("check: " + note)
    Out.muted(f"{row.provider} {row.model} · {row.duration_ms} ms · stored in run_summaries ({row.id})")
    return SUCCESS if result.summary is not None else TESTS_FAILED


# ---- aqt standards -------------------------------------------------------------------------------------------

def _standards_dir(cli) -> str:
    from .cli import Paths
    return os.path.join(Paths(cli).root, "standards")


def _selected(sources, cli):
    only = cli.get("standard")
    return [s for s in sources.load() if not only or s.id.lower() == only.lower()]


def cmd_standards(cli) -> int:
    from .standards import StandardSources
    Out = _out()
    sources = StandardSources(_standards_dir(cli))
    sub = cli.positional[0] if cli.positional else "list"

    if sub == "fetch":
        ok = True
        for source in _selected(sources, cli):
            try:
                Out.ok(f"{source.id:<9} {source.version:<9} {sources.fetch(source)}")
            except (OSError, ValueError) as ex:
                ok = False
                Out.error(f"{source.id} {source.version}: {ex}")
        return SUCCESS if ok else CONFIGURATION_ERROR

    store = _store(cli)
    if sub == "ingest":
        from .ai.ingest import StandardsIngestor
        from .ai import options as opt
        ingestor = StandardsIngestor(store, sources, _factory(store, cli, opt.RULE_DRAFTING))
        for source in _selected(sources, cli):
            r = ingestor.ingest(source, embed=not cli.has("no-embed"), progress=lambda m: None if "embedded" in m else Out.muted(m))
            Out.ok(f"{r.standard:<9} {r.version:<9} {r.clauses} clauses, {r.embedded} embedded, {r.duration_ms / 1000:.1f} s")
            for p in r.problems:
                Out.notice(p)
        return SUCCESS
    if sub == "list":
        docs = store.list_standards()
        if not docs:
            Out.muted("No standards in the store yet: aqt standards fetch && aqt standards ingest")
        for d in docs:
            print(f"{d.id:<9} {d.version:<9} {d.language}  {d.clause_count:>4} clauses  {d.title}")
        return SUCCESS
    if sub == "search":
        query = " ".join(cli.positional[1:])
        if not query:
            raise ConfigurationError('Usage: aqt standards search "<text>" [--standard ID]')
        from .ai import options as opt
        from .ai.chat import ProviderError
        vector = None
        try:
            vector = _factory(store, cli, opt.RULE_DRAFTING).embed([query])[0]
        except (ProviderError, TimeoutError):
            Out.muted("(Ollama not reachable: searching by words only)")
        for hit in store.search_clauses(query, vector, cli.get("standard"), 10):
            how = "words+meaning" if hit.by_words and hit.by_meaning else ("words" if hit.by_words else "meaning")
            print(f"{hit.clause.key:<28} {hit.clause.title[:70]:<70} {how}")
        return SUCCESS
    if sub == "show":
        if len(cli.positional) < 2:
            raise ConfigurationError("Usage: aqt standards show <clause-key>  (e.g. BG-IG:1.3.16:6.3.1.2)")
        key = cli.positional[1]
        clause = store.get_clause(key)
        if clause is None:
            raise ConfigurationError(f"No clause {key}")
        for a in store.get_ancestors(clause):
            Out.muted(f"{a.number} {a.title}")
        Out.header(f"{clause.number} {clause.title}{f'  (p. {clause.page})' if clause.page else ''}")
        print(clause.text)
        for r in store.list_rules(clause_key=key):
            Out.muted(f"rule {r.id} [{r.status}] {r.level}: {r.requirement}")
        return SUCCESS
    raise ConfigurationError('Usage: aqt standards fetch|ingest [--standard ID] [--no-embed]|list|search "<text>"|show <clause-key>')


# ---- aqt rules -----------------------------------------------------------------------------------------------

def _print_rule(r):
    Out = _out()
    print()
    line = f"{r.id}  [{r.status}]  {r.level} · {r.service} · {r.standard} {r.clause_number}  {r.title}"
    (Out.error if r.status == "invalid" else Out.ok)(line)
    print(f"   requirement: {r.requirement}")
    print(f'   quote: "{r.quote}"')
    if r.quote_english:
        print(f'   quote (EN, AI translation): "{r.quote_english}"')
    if r.operation:
        print(f"   operation: {r.operation}")
    c = r.check_json or {}
    if c:
        status = f" [status {'/'.join(map(str, c.get('expectStatus') or []))}]" if c.get("expectStatus") else ""
        codes = f" [{'/'.join(c.get('expectErrorCodes') or [])}]" if c.get("expectErrorCodes") else ""
        print(f"   check: {c.get('kind')} — {c.get('description')}{status}{codes}")
    if r.notes:
        print(f"   notes: {r.notes}")
    for n in r.check_notes:
        Out.muted("   check: " + n)


def cmd_rules(cli) -> int:
    from .ai.rules import ReviewError, RuleBook, RuleDrafter
    Out = _out()
    sub = cli.positional[0] if cli.positional else "list"
    rule_id = cli.positional[1] if len(cli.positional) > 1 else None
    store = _store(cli)
    book = RuleBook(store)

    if sub == "draft":
        if rule_id is None:
            raise ConfigurationError("Usage: aqt rules draft <clause-key> [--provider anthropic|ollama] [--model name]")
        from .ai import options as opt
        factory = _factory(store, cli, opt.RULE_DRAFTING)
        settings = factory.describe(opt.RULE_DRAFTING)
        Out.muted(f"Drafting rules for {rule_id} with {settings.provider} {settings.model} ...")
        try:
            result = RuleDrafter(store, factory).draft(rule_id, f"{os.environ.get('USER', 'cli')} (aqt rules draft)")
        except ValueError as ex:
            raise ConfigurationError(str(ex)) from ex
        if result.status != "ok":
            Out.error(result.error or result.status)
            return TESTS_FAILED
        for r in result.rules:
            _print_rule(r)
        if result.skipped:
            Out.muted("not drafted: " + result.skipped)
        Out.muted(f"{sum(r.status == 'draft' for r in result.rules)} draft(s) for review, "
                  f"{sum(r.status == 'invalid' for r in result.rules)} rejected by the checks · {result.duration_ms / 1000:.1f} s")
        return SUCCESS
    if sub == "list":
        for r in store.list_rules(cli.get("status"), cli.get("standard")):
            print(f"{r.id:<14} {r.status:<9} {r.level:<6} {r.service:<6} {r.standard} {r.clause_number:<9} {r.title}")
        return SUCCESS
    if sub == "show":
        rule = store.get_rule(rule_id or "")
        if rule is None:
            raise ConfigurationError(f"No rule {rule_id}")
        _print_rule(rule)
        for h in store.get_rule_history(rule.id):
            Out.muted(f"  {h.time_utc:%Y-%m-%d %H:%M} {h.action:<9} by {h.reviewer}{': ' + h.note if h.note else ''}")
        return SUCCESS
    if sub in ("approve", "reject", "reopen"):
        reviewer = cli.get("by")
        if not reviewer or not rule_id:
            raise ConfigurationError(f"Usage: aqt rules {sub} <id> --by <your name> [--note text]")
        try:
            rule = {"approve": lambda: book.approve(rule_id, reviewer, cli.get("note")),
                    "reject": lambda: book.reject(rule_id, reviewer, cli.get("note") or ""),
                    "reopen": lambda: book.reopen(rule_id, reviewer, cli.get("note"))}[sub]()
        except ReviewError as ex:
            raise ConfigurationError(str(ex)) from ex
        Out.ok(f"{rule.id} is now {rule.status}")
        return SUCCESS
    if sub == "export":
        from .cli import Paths
        written, removed = book.export(os.path.join(Paths(cli).root, "rules"))
        Out.ok(f"{written} approved rule(s) written to rules/, {removed} stale file(s) removed")
        return SUCCESS
    raise ConfigurationError("Usage: aqt rules draft|list|show|approve|reject|reopen|export")


# ---- aqt scenarios / aqt coverage ------------------------------------------------------------------------------

def _find_draft(store, key: str):
    """A draft by its id (or the first 8+ characters of it) or by scenario id (the newest open one)."""
    key = (key or "").strip()
    drafts = store.list_scenario_drafts()
    by_scenario = [d for d in drafts if d.scenario_id.lower() == key.lower()]
    if by_scenario:
        return next((d for d in by_scenario if d.status in ("draft", "failed")), by_scenario[0])
    matches = [d for d in drafts if len(key) >= 8 and str(d.id).startswith(key.lower())]
    if len(matches) != 1:
        raise ConfigurationError(f"No scenario draft '{key}' (use the scenario id or the draft id from aqt scenarios drafts)")
    return matches[0]


def _print_draft(d, full: bool):
    Out = _out()
    print()
    line = f"{d.scenario_id}  [{d.status}]  rule {d.rule_id} · dry run {d.dry_run_outcome or '-'} · {str(d.id)[:8]}  {d.title}"
    (Out.error if d.status == "failed" else Out.ok)(line)
    if d.mock_gap:
        print(f"   MockBank gap (model's view): {d.mock_gap}")
    for n in d.check_notes:
        Out.muted("   check: " + n)
    if d.reviewed_by:
        Out.muted(f"   {d.status} by {d.reviewed_by}{': ' + d.review_note if d.review_note else ''}{' -> ' + d.file_path if d.file_path else ''}")
    if full:
        print()
        print(d.yaml)


def cmd_scenarios(cli) -> int:
    from .ai.scenario_drafting import ScenarioDrafter, ScenarioReview, ScenarioReviewError
    from .cli import Paths
    from .dryrun import MockDryRun
    Out = _out()
    sub = cli.positional[0] if cli.positional else "drafts"
    arg = cli.positional[1] if len(cli.positional) > 1 else None
    paths = Paths(cli)
    store = _store(cli)

    if sub == "draft":
        if arg is None:
            raise ConfigurationError("Usage: aqt scenarios draft <approved rule id> [--provider anthropic|ollama] [--model name]")
        from .ai import options as opt
        factory = _factory(store, cli, opt.SCENARIO_DRAFTING)
        settings = factory.describe(opt.SCENARIO_DRAFTING)
        Out.muted(f"Drafting a scenario for {arg} with {settings.provider} {settings.model}; dry runs on an in-process MockBank ...")
        with MockDryRun(paths.banks, paths.scenarios) as dry_run:
            try:
                draft = ScenarioDrafter(store, factory, paths.scenarios, dry_run).draft(arg, f"{os.environ.get('USER', 'cli')} (aqt scenarios draft)")
            except ValueError as ex:
                raise ConfigurationError(str(ex)) from ex
        _print_draft(draft, full=True)
        return SUCCESS if draft.status == "draft" else TESTS_FAILED
    if sub == "drafts":
        for d in store.list_scenario_drafts(cli.get("status"), cli.get("rule")):
            print(f"{str(d.id)[:8]}  {d.scenario_id:<12} {d.status:<9} {d.rule_id:<12} {(d.dry_run_outcome or '-'):<8} {d.title}")
        return SUCCESS
    if sub == "show":
        _print_draft(_find_draft(store, arg), full=True)
        return SUCCESS
    if sub in ("approve", "reject"):
        reviewer = cli.get("by")
        if not reviewer or not arg:
            raise ConfigurationError(f"Usage: aqt scenarios {sub} <draft> --by <your name> [--note text]")
        draft = _find_draft(store, arg)
        review = ScenarioReview(store, paths.scenarios)
        try:
            draft = review.approve(draft.id, reviewer, cli.get("note")) if sub == "approve" else review.reject(draft.id, reviewer, cli.get("note") or "")
        except ScenarioReviewError as ex:
            raise ConfigurationError(str(ex)) from ex
        Out.ok(f"{draft.scenario_id} is now {draft.status}" + (f": {draft.file_path} (linked to {draft.rule_id})" if draft.file_path else ""))
        return SUCCESS
    raise ConfigurationError("Usage: aqt scenarios draft|drafts|show|approve|reject")


def cmd_coverage(cli) -> int:
    from .cli import Paths
    from .store import coverage
    Out = _out()
    result = coverage.compute(_store(cli), Paths(cli).scenarios, include_drafts=cli.has("drafts"))
    for r in result.rules:
        line = f"{r.rule_id:<14} {r.status:<9} {r.level:<6} {r.service:<6} {r.title}"
        (Out.ok if r.covered else Out.muted)(line)
        for s in r.scenarios:
            results = ", ".join(f"{bank} {outcome}" for bank, (outcome, _) in sorted(s.results.items())) or "never run"
            print(f"        {s.id:<12} {results}{'' if s.file else '  (no scenario file)'}")
        if r.open_drafts:
            Out.muted(f"        {r.open_drafts} scenario draft(s) waiting for review")
    for p in result.problems:
        Out.error(p)
    per_service = " · ".join(f"{s} {covered}/{approved}" for s, (approved, covered) in result.by_service.items() if approved)
    Out.muted(f"{result.covered_count} of {result.approved_count} approved rule(s) have a scenario" + (f" ({per_service})." if per_service else "."))
    return SUCCESS


# ---- aqt ask ---------------------------------------------------------------------------------------------------

def _print_question(q, show_tools: bool):
    Out = _out()
    if show_tools:
        for t in q.tool_calls:
            Out.muted(f"  · {t['tool']}({json.dumps(t['arguments'], ensure_ascii=False)})")
    print()
    if q.answer:
        print(q.answer)
    else:
        Out.error(q.error or "No answer.")
    print()
    for n in q.citation_notes:
        Out._write("  check: " + n, "dark_yellow")
    Out.muted(f"{q.status} · {q.provider} {q.model} · {len(q.tool_calls)} tool call(s) · {q.duration_ms / 1000:.1f} s · question {str(q.id)[:8]}")


def cmd_ask(cli) -> int:
    from .ai.agent import AskAgent
    Out = _out()
    store = _store(cli)
    if cli.positional and cli.positional[0] == "history":
        for q in store.list_agent_questions(int(cli.get("limit") or 20)):
            print(f"{str(q.id)[:8]}  {q.asked_utc:%Y-%m-%d %H:%M}  {q.status:<10} {q.asked_by or '-':<12} {q.question[:90]}")
        return SUCCESS
    question = " ".join(cli.positional).strip()
    if not question:
        raise ConfigurationError('Usage: aqt ask "<question>" [--provider anthropic|ollama] [--model name] | aqt ask history')
    from .ai import options as opt
    factory = _factory(store, cli, opt.ASK)
    settings = factory.describe(opt.ASK)
    Out.muted(f"Asking {settings.provider} {settings.model} (read-only tools over the store) ...")
    answer = AskAgent(store, factory).ask(question, f"{os.environ.get('USER', 'cli')} (aqt ask)",
                                          on_tool=lambda name, args: Out.muted(f"  · {name}({json.dumps(args, ensure_ascii=False)})"))
    _print_question(answer.row, show_tools=False)
    return SUCCESS if answer.row.status != "failed" else TESTS_FAILED
