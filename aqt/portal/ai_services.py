"""
Portal services for the store and the AI layer: writing runs and events to PostgreSQL in the background, AI run
summaries, and standards/rules (search, drafting, review). All optional: without a database (or without the `ai`
extra) the portal works exactly as before and these pages say how to turn them on.
"""

import os
import queue
import threading
import traceback

from ..results import Outcome


class StoreRecorder:
    """Writes portal runs and events to the store on a background thread, one write at a time. Callers never wait for
    the database, and a database problem never affects a run: it is logged, and the run folder can be imported later."""

    def __init__(self, store):
        self.store = store
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


def try_create(connection: str | None, root_directory: str, auto_summarize: bool = False):
    """(recorder, summaries, rules, problem). Everything None with a reason when the store/AI layer is not available."""
    try:
        from ..store import AqtStore, DOCKER_COMPOSE_DEFAULT
    except ImportError as ex:
        return None, None, None, f"The store and AI pages need the AI extra: pip install -e '.[ai]' ({ex})"
    store = AqtStore(connection or DOCKER_COMPOSE_DEFAULT)
    try:
        applied = store.migrate()
        if applied:
            print(f"  store: applied migrations {', '.join(applied)}")
    except Exception as ex:  # noqa: BLE001 - the portal still works without the database
        return None, None, None, f"The database is not reachable ({ex}). Start it with 'docker compose up -d' or set AQT_DB."
    return StoreRecorder(store), SummaryService(store, auto_summarize), RuleService(store, root_directory), None
