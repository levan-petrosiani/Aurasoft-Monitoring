"""
Portal services for the store and the AI layer: writing runs and events to PostgreSQL in the background, AI run
summaries, and standards/rules (search, drafting, review). All optional: without a database (or without the `ai`
extra) the portal works exactly as before and these pages say how to turn them on.
"""

import os
import queue
import threading
import traceback
import uuid

from ..results import Outcome


class StoreRecorder:
    """Writes portal runs and events to the store on a background thread, one write at a time. Callers never wait for
    the database, and a database problem never affects a run: it is logged, and the run folder can be imported later."""

    def __init__(self, store, root_directory: str = "."):
        self.store = store
        self.root_directory = root_directory
        self._queue: "queue.Queue" = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="aqt-portal-store").start()

    def save_run(self, run, report_directory: str):
        from ..portal.services import QUEUED, RUNNING
        info = self._info(run, report_directory)  # snapshot now: the run keeps changing while the write waits
        result = None if run.record.status in (QUEUED, RUNNING) else run.result
        self._enqueue(f"save run {run.id}", lambda: self.store.save_run(info, result))

    def event(self, type_: str, message: str, run_id: str | None = None, level: str = "Info", data=None):
        self._enqueue(f"event {type_}", lambda: self.store.add_event("portal", type_, message, run_id, level, data))

    def delete_run(self, run_id: str):
        self._enqueue(f"delete run {run_id}", lambda: self.store.delete_run(run_id))

    def run_finished(self, run_id: str):
        """After the final save of a run: compute and send alerts (regression, fixed, bank down, run failed)."""
        from ..monitor import alerts, config
        root = self.root_directory

        def process():
            try:
                settings = config.load(root)
            except Exception as ex:  # noqa: BLE001 - a broken monitoring.yaml still records alerts, without notifying
                print(f"Portal: monitoring.yaml: {ex}")
                settings = config.MonitoringConfig(alerts=config.AlertsConfig(notify=[]))
            alerts.process_run(self.store, run_id, settings)

        self._enqueue(f"alerts {run_id}", process)

    def then(self, what: str, action):
        """Runs `action` after every write queued so far (e.g. summarize once the run is saved)."""
        self._enqueue(what, action)

    def _enqueue(self, what, write):
        self._queue.put((what, write))

    def _worker(self):
        while True:
            what, write = self._queue.get()
            try:
                write()
            except Exception as ex:  # noqa: BLE001 - logged; the run folder can be imported later
                print(f"Portal: store write failed ({what}): {ex}; the run folder can be imported later with 'aqt db import'")

    @staticmethod
    def _info(run, report_directory):
        from ..store import RunInfo
        record = run.record
        parts = record.id.split("-")
        bic = run.result.bank_bic if run.result else (parts[2] if len(parts) == 4 else record.request.bank)
        return RunInfo(id=record.id, source="portal", status=record.status, bank_bic=bic, bank_name=record.bank_name,
                       created_utc=record.created_utc, started_utc=record.started_utc, finished_utc=record.finished_utc,
                       suites=list(record.request.suites), error=record.error, report_directory=report_directory,
                       options_json=record.request.to_dict())

    @staticmethod
    def level(outcome) -> str:
        return "Warning" if outcome in (Outcome.FAILED, Outcome.ERROR) else "Info"


class _Background:
    """Runs one job per key at a time on a daemon thread and remembers which keys are busy."""

    def __init__(self):
        self._busy: dict[str, str] = {}
        self._lock = threading.Lock()

    def busy(self, key: str) -> str | None:
        with self._lock:
            return self._busy.get(key)

    def start(self, key: str, label: str, job) -> bool:
        with self._lock:
            if key in self._busy:
                return False
            self._busy[key] = label

        def run():
            try:
                job()
            except Exception:  # noqa: BLE001 - shown as "failed" by the job itself where possible; logged here
                traceback.print_exc()
            finally:
                with self._lock:
                    self._busy.pop(key, None)

        threading.Thread(target=run, daemon=True, name=f"aqt-ai-{key}").start()
        return True


class SummaryService:
    """AI run summaries for the Run page: start one (Claude or local), read the latest, one at a time per run."""

    def __init__(self, store, auto_summarize: bool = False, auto_provider: str | None = None):
        from ..ai import options as opt
        self.store = store
        self.options = opt.AiOptions.from_environment()
        self.auto_summarize = auto_summarize
        self.auto_provider = auto_provider or self.options.for_feature(opt.SUMMARY).provider
        self._jobs = _Background()

    def running_provider(self, run_id: str) -> str | None:
        return self._jobs.busy(run_id)

    def model_for(self, provider: str) -> str:
        from ..ai import options as opt
        return self.options.with_provider(opt.SUMMARY, provider).for_feature(opt.SUMMARY).model

    def latest(self, run_id: str):
        try:
            return self.store.get_latest_run_summary(run_id)
        except Exception as ex:  # noqa: BLE001 - the page still renders without the panel's content
            print(f"Portal: could not read the summary of {run_id}: {ex}")
            return None

    def start(self, run_id: str, provider: str, triggered_by: str) -> bool:
        from ..ai import options as opt
        from ..ai.chat import ChatFactory, StoreAuditSink
        from ..ai.summaries import RunSummarizer

        def job():
            factory = ChatFactory(self.options.with_provider(opt.SUMMARY, provider), StoreAuditSink(self.store, print), self.store.masker)
            RunSummarizer(self.store, factory).summarize(run_id, triggered_by)

        return self._jobs.start(run_id, provider, job)


class RuleService:
    """Standards and rules for the portal: clause search, drafting in the background, review actions, export."""

    def __init__(self, store, root_directory: str):
        from ..ai import options as opt
        from ..ai.rules import RuleBook
        self.store = store
        self.options = opt.AiOptions.from_environment()
        self.book = RuleBook(store)
        self.rules_directory = os.path.join(root_directory, "rules")
        self._jobs = _Background()
        self._last: dict[str, object] = {}

    def drafting_provider(self, clause_key: str) -> str | None:
        return self._jobs.busy(clause_key)

    def last_draft(self, clause_key: str):
        return self._last.get(clause_key)

    def model_for(self, provider: str) -> str:
        from ..ai import options as opt
        return self.options.with_provider(opt.RULE_DRAFTING, provider).for_feature(opt.RULE_DRAFTING).model

    def search(self, query: str, standard: str | None = None):
        from ..ai.chat import ChatFactory, InMemoryAuditSink, ProviderError
        vector = None
        try:
            vector = ChatFactory(self.options, InMemoryAuditSink()).embed([query])[0]
        except (ProviderError, TimeoutError) as ex:
            print(f"Portal: clause search by words only: {ex}")
        return self.store.search_clauses(query, vector, standard, 15)

    def start_draft(self, clause_key: str, provider: str, triggered_by: str) -> bool:
        from ..ai import options as opt
        from ..ai.chat import ChatFactory, StoreAuditSink
        from ..ai.rules import DraftResult, RuleDrafter

        def job():
            try:
                factory = ChatFactory(self.options.with_provider(opt.RULE_DRAFTING, provider), StoreAuditSink(self.store, print), self.store.masker)
                self._last[clause_key] = RuleDrafter(self.store, factory).draft(clause_key, triggered_by)
            except Exception as ex:  # noqa: BLE001 - shown next to the clause
                self._last[clause_key] = DraftResult(clause_key, [], None, "failed", str(ex), 0)
                raise

        return self._jobs.start(clause_key, provider, job)

    def review(self, action) -> str | None:
        """Runs a review action; returns its error message instead of raising (shown next to the rule)."""
        from ..ai.rules import ReviewError
        try:
            action(self.book)
            return None
        except ReviewError as ex:
            return str(ex)

    def export(self):
        return self.book.export(self.rules_directory)


class ScenarioService:
    """Scenario drafts for the portal: drafting from an approved rule in the background (dry runs on an in-process
    MockBank only), review actions (approve writes scenarios/<suite>/..., edit re-checks) and coverage."""

    def __init__(self, store, root_directory: str):
        from ..ai import options as opt
        self.store = store
        self.options = opt.AiOptions.from_environment()
        self.scenarios_directory = os.path.join(root_directory, "scenarios")
        self.banks_directory = os.path.join(root_directory, "banks")
        self._jobs = _Background()
        self._last_error: dict[str, str] = {}

    def drafting_provider(self, rule_id: str) -> str | None:
        return self._jobs.busy(rule_id)

    def last_error(self, rule_id: str) -> str | None:
        return self._last_error.get(rule_id)

    def model_for(self, provider: str) -> str:
        from ..ai import options as opt
        return self.options.with_provider(opt.SCENARIO_DRAFTING, provider).for_feature(opt.SCENARIO_DRAFTING).model

    def start_draft(self, rule_id: str, provider: str, triggered_by: str) -> bool:
        from ..ai import options as opt
        from ..ai.chat import ChatFactory, StoreAuditSink
        from ..ai.scenario_drafting import ScenarioDrafter
        from ..dryrun import MockDryRun

        def job():
            self._last_error.pop(rule_id, None)
            try:
                factory = ChatFactory(self.options.with_provider(opt.SCENARIO_DRAFTING, provider), StoreAuditSink(self.store, print), self.store.masker)
                with MockDryRun(self.banks_directory, self.scenarios_directory) as dry_run:
                    ScenarioDrafter(self.store, factory, self.scenarios_directory, dry_run).draft(rule_id, triggered_by)
            except Exception as ex:  # noqa: BLE001 - shown on the Drafts page
                self._last_error[rule_id] = f"{type(ex).__name__}: {ex}"
                raise

        return self._jobs.start(rule_id, provider, job)

    def review(self, action) -> str | None:
        """Runs action(review); returns its error message instead of raising (shown next to the draft)."""
        from ..ai.scenario_drafting import ScenarioReview, ScenarioReviewError
        from ..dryrun import MockDryRun
        try:
            with MockDryRun(self.banks_directory, self.scenarios_directory) as dry_run:
                action(ScenarioReview(self.store, self.scenarios_directory, dry_run, self.store.masker))
            return None
        except ScenarioReviewError as ex:
            return str(ex)

    def coverage(self, include_drafts: bool = False):
        from ..store import coverage
        return coverage.compute(self.store, self.scenarios_directory, include_drafts)


class AskService:
    """The ask-agent for the portal: each question runs in the background; answers are read from agent_questions."""

    def __init__(self, store):
        from ..ai import options as opt
        self.store = store
        self.options = opt.AiOptions.from_environment()
        self._jobs = _Background()
        self._pending: dict[str, dict] = {}       # job key -> question, provider, who, tools called so far
        self._errors: dict[str, str] = {}

    def model_for(self, provider: str) -> str:
        from ..ai import options as opt
        return self.options.with_provider(opt.ASK, provider).for_feature(opt.ASK).model

    def pending(self) -> list[dict]:
        return [dict(p, key=k) for k, p in list(self._pending.items()) if self._jobs.busy(k)]

    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    def start(self, question: str, provider: str, asked_by: str) -> str:
        from ..ai import options as opt
        from ..ai.agent import AskAgent
        from ..ai.chat import ChatFactory, StoreAuditSink
        key = uuid.uuid4().hex[:12]
        state = {"question": question, "provider": provider, "asked_by": asked_by, "tools": []}
        self._pending[key] = state

        def job():
            try:
                factory = ChatFactory(self.options.with_provider(opt.ASK, provider), StoreAuditSink(self.store, print), self.store.masker)
                AskAgent(self.store, factory).ask(question, asked_by, on_tool=lambda name, args: state["tools"].append(name))
            except Exception as ex:  # noqa: BLE001 - shown on the Ask page
                self._errors[key] = f"{question[:80]}: {type(ex).__name__}: {ex}"
                raise
            finally:
                self._pending.pop(key, None)

        self._jobs.start(key, provider, job)
        return key


class MonitorService:
    """The portal's monitor: every 30 s it reads monitoring.yaml, queues due schedules as unattended runs, and once a
    day deletes old exchanges (retention). Schedule times and results are kept in monitor_state."""

    TICK_SECONDS = 30

    def __init__(self, store, root_directory: str):
        self.store = store
        self.root_directory = root_directory
        self.runs = None          # RunService, set by the app
        self.enabled = False
        self.problem: str | None = None
        self.last_prune: str | None = None

    def start(self, runs):
        self.runs, self.enabled = runs, True
        threading.Thread(target=self._loop, daemon=True, name="aqt-monitor").start()

    def config(self):
        from ..monitor import config
        try:
            self.problem = None
            return config.load(self.root_directory)
        except Exception as ex:  # noqa: BLE001 - shown on the Monitoring page
            self.problem = str(ex)
            return config.MonitoringConfig()

    def overview(self):
        """[(schedule, state row or None, next due, live status)] for the page."""
        from ..monitor import config
        from .services import utc_now
        state = self.store.get_monitor_state()
        now = utc_now()
        rows = []
        for s in self.config().schedules:
            st = state.get(s.name)
            live = self.runs.get(st.last_run_id) if (self.runs and st and st.last_run_id) else None
            rows.append((s, st, config.next_due(s, st.last_started_utc if st else None, now), live.record.status if live else (st.last_status if st else None)))
        return rows, state.get(config.RETENTION_STATE)

    def run_now(self, name: str, who: str) -> str | None:
        schedule = next((s for s in self.config().schedules if s.name == name), None)
        if schedule is None:
            return f"No schedule '{name}' in monitoring.yaml"
        return self._start(schedule, who)

    def _start(self, schedule, who: str = "monitor") -> str | None:
        from ..store.entities import MonitorStateRow
        from .services import RunRequest, utc_now
        request = RunRequest(bank=schedule.bank, suites=list(schedule.suites or []), scenario_ids=list(schedule.scenarios or []),
                             use_mock_bank=schedule.with_mock, auto_sca=schedule.with_mock, unattended=True, schedule=schedule.name)
        try:
            run = self.runs.enqueue(request)
        except Exception as ex:  # noqa: BLE001 - e.g. an unknown bank: recorded on the schedule
            self.store.set_monitor_state(MonitorStateRow(schedule.name, utc_now(), None, "Failed", f"{type(ex).__name__}: {ex}"))
            return str(ex)
        self.store.set_monitor_state(MonitorStateRow(schedule.name, utc_now(), run.id, "Queued", None))
        self.store.add_event("monitor", "schedule.started", f"Schedule {schedule.name} ({schedule.describe()}) queued run {run.id} by {who}", run.id)
        return None

    def _loop(self):
        import time as _time
        while True:
            try:
                self.tick()
            except Exception as ex:  # noqa: BLE001 - the monitor keeps going
                print(f"Monitor: {type(ex).__name__}: {ex}")
            _time.sleep(self.TICK_SECONDS)

    def tick(self):
        from ..monitor import config
        from ..store.entities import MonitorStateRow
        from .services import COMPLETED, FAILED, CANCELLED, utc_now
        settings = self.config()
        state = self.store.get_monitor_state()
        now = utc_now()
        for s in settings.schedules:
            st = state.get(s.name)
            live = self.runs.get(st.last_run_id) if st and st.last_run_id else None
            if live is not None and live.is_active:
                continue  # still running: never queue the same schedule twice
            if st is not None and live is not None and st.last_status != live.record.status:
                st.last_status = live.record.status
                st.last_error = live.record.error if live.record.status in (FAILED, CANCELLED) else None
                self.store.set_monitor_state(st)
            if config.is_due(s, st.last_started_utc if st else None, now):
                self._start(s)
        retention = state.get(config.RETENTION_STATE)
        if retention is None or (now - retention.last_started_utc).total_seconds() >= 24 * 3600:
            deleted = self.store.prune_exchanges(settings.retention.exchanges_days)
            note = f"deleted {deleted} exchange(s) older than {settings.retention.exchanges_days} days"
            self.store.set_monitor_state(MonitorStateRow(config.RETENTION_STATE, now, None, COMPLETED, note))
            if deleted:
                self.store.add_event("monitor", "retention.pruned", note)


def try_create(connection: str | None, root_directory: str, auto_summarize: bool = False):
    """(recorder, summaries, rules, scenarios, ask, monitor, problem). Everything None with a reason when the store/AI layer is not available."""
    try:
        from ..store import AqtStore, DOCKER_COMPOSE_DEFAULT
    except ImportError as ex:
        return None, None, None, None, None, None, f"The store and AI pages need the AI extra: pip install -e '.[ai]' ({ex})"
    store = AqtStore(connection or DOCKER_COMPOSE_DEFAULT)
    try:
        applied = store.migrate()
        if applied:
            print(f"  store: applied migrations {', '.join(applied)}")
    except Exception as ex:  # noqa: BLE001 - the portal still works without the database
        return None, None, None, None, None, None, f"The database is not reachable ({ex}). Start it with 'docker compose up -d' or set AQT_DB."
    return (StoreRecorder(store, root_directory), SummaryService(store, auto_summarize), RuleService(store, root_directory),
            ScenarioService(store, root_directory), AskService(store), MonitorService(store, root_directory), None)
