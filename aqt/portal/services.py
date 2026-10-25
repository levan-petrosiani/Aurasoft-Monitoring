"""
Portal services: the catalogue (banks, scenarios), MockBank hosted on demand, and the run queue. Runs execute one
at a time (manual SCA listens on one fixed localhost callback port) and each run is stored in its own folder:
run.json, summary.json, report.html, junit.xml, so history survives restarts.
"""

import json
import os
import queue
import shutil
import socket
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .. import profiles, reporting, scenarios
from ..context import RunContext
from ..errors import ConfigurationError, OperationCancelled
from ..oauth import allows_headless_sca, headless_authorizer, is_loopback_host
from ..profiles import BankProfile
from ..results import Outcome, RunResult, ScenarioResult, format_rate, format_time, parse_time
from ..runner import ScenarioRunner, is_preflight
from ..scenarios import Scenario, TemplateLibrary


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---- catalogue -------------------------------------------------------------------------------------------------

@dataclass
class BankEntry:
    key: str
    profile: BankProfile | None
    error: str | None

    @property
    def bic(self) -> str:
        return self.profile.bank.bic if self.profile else self.key

    @property
    def name(self) -> str:
        return self.profile.bank.name if self.profile else self.key


class CatalogService:
    """Bank profiles and scenarios, read fresh from disk on every call so YAML edits show up without a restart."""

    def __init__(self, root_directory: str):
        self.root_directory = os.path.abspath(root_directory)

    @property
    def banks_directory(self) -> str:
        return os.path.join(self.root_directory, "banks")

    @property
    def scenarios_directory(self) -> str:
        return os.path.join(self.root_directory, "scenarios")

    def get_banks(self) -> list[BankEntry]:
        """All profiles; a broken file is returned with its error instead of hiding the others."""
        result = []
        for file in profiles.list_profiles(self.banks_directory):
            key = os.path.splitext(os.path.basename(file))[0]
            try:
                result.append(BankEntry(key, profiles.load_file(file), None))
            except ConfigurationError as ex:
                result.append(BankEntry(key, None, str(ex)))
        return result

    def load_bank(self, key: str) -> BankProfile:
        return profiles.load(key, self.banks_directory)

    def get_scenarios(self) -> list[Scenario]:
        return scenarios.load_all(self.scenarios_directory)

    def get_templates(self) -> TemplateLibrary:
        return TemplateLibrary.load(self.scenarios_directory)

    @staticmethod
    def is_local(profile: BankProfile) -> bool:
        """True for MockBank or another server on this machine."""
        return is_loopback_host(urlsplit(profile.endpoints.xs2a).hostname)

    @staticmethod
    def allows_headless_sca(profile: BankProfile) -> bool:
        """Automatic SCA is offered for local servers and for profiles with features.headlessSca: true."""
        return allows_headless_sca(profile)


# ---- MockBank ----------------------------------------------------------------------------------------------------

class MockBankService:
    """Hosts MockBank inside the portal process on demand."""

    def __init__(self, xs2a_url: str = "https://localhost:7443/"):
        self.xs2a_url = xs2a_url
        parts = urlsplit(xs2a_url)
        self._host_name, self._port = parts.hostname, parts.port or 443
        self._lock = threading.Lock()
        self._host = None
        self.dashboard_url = "http://localhost:7080/"

    @property
    def is_hosted_here(self) -> bool:
        return self._host is not None

    def is_reachable(self) -> bool:
        """True when something (this portal, `aqt mock`, the C# MockBank) listens on the MockBank port."""
        try:
            with socket.create_connection((self._host_name, self._port), timeout=1):
                return True
        except OSError:
            return False

    def ensure_running(self):
        """Starts MockBank here unless one is already reachable."""
        from ..mockbank import MockBankHost, MockBankOptions

        with self._lock:
            if self._host is not None or self.is_reachable():
                return
            host = MockBankHost(MockBankOptions.load({"https_port": self._port}), quiet=True)
            try:
                host.start()
            except OSError as ex:
                raise ConfigurationError(f"Cannot start MockBank: {ex}") from ex
            self._host = host
            self.dashboard_url = f"{host.options.http_base_url}/"

    def stop(self):
        with self._lock:
            if self._host is not None:
                self._host.stop()
                self._host = None


# ---- runs --------------------------------------------------------------------------------------------------------

QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED = "Queued", "Running", "Completed", "Failed", "Cancelled"
UNATTENDED_SCA_REASON = "Needs a person for SCA (bank login); not run in an unattended monitoring run"


@dataclass
class RunRequest:
    """What the user asked for on the "New run" page."""
    bank: str = ""
    suites: list[str] = field(default_factory=list)
    scenario_ids: list[str] = field(default_factory=list)
    # Host MockBank in the portal for this run (local profiles only).
    use_mock_bank: bool = False
    # Complete SCA without a browser (auto-approving test servers only).
    auto_sca: bool = False
    preflight_gate: bool = True
    # Started by the monitor: nobody is there to log in, so scenarios with an SCA step are skipped (unless SCA is
    # automatic, i.e. MockBank).
    unattended: bool = False
    schedule: str | None = None

    def to_dict(self):
        d = {"bank": self.bank, "suites": self.suites, "scenarioIds": self.scenario_ids,
             "useMockBank": self.use_mock_bank, "autoSca": self.auto_sca, "preflightGate": self.preflight_gate}
        if self.unattended:
            d.update(unattended=True, schedule=self.schedule)
        return d

    @staticmethod
    def from_dict(d):
        return RunRequest(d.get("bank", ""), list(d.get("suites") or []), list(d.get("scenarioIds") or []),
                          bool(d.get("useMockBank")), bool(d.get("autoSca")), d.get("preflightGate", True) is not False,
                          bool(d.get("unattended")), d.get("schedule"))


@dataclass
class RunRecord:
    """Stored next to the reports as run.json; together with summary.json it describes a run."""
    id: str
    request: RunRequest
    status: str = QUEUED
    created_utc: datetime | None = None
    started_utc: datetime | None = None
    finished_utc: datetime | None = None
    error: str | None = None
    bank_name: str | None = None
    passed: int = 0
    failed: int = 0
    errors: int = 0
    blocked: int = 0
    skipped: int = 0
    pass_rate: float = 0

    def to_dict(self):
        return {"id": self.id, "request": self.request.to_dict(), "status": self.status,
                "createdUtc": format_time(self.created_utc), "startedUtc": format_time(self.started_utc),
                "finishedUtc": format_time(self.finished_utc), "error": self.error, "bankName": self.bank_name,
                "passed": self.passed, "failed": self.failed, "errors": self.errors, "blocked": self.blocked,
                "skipped": self.skipped, "passRate": self.pass_rate}

    @staticmethod
    def from_dict(d):
        return RunRecord(
            id=d["id"], request=RunRequest.from_dict(d.get("request") or {}), status=d.get("status", QUEUED),
            created_utc=parse_time(d.get("createdUtc")), started_utc=parse_time(d.get("startedUtc")),
            finished_utc=parse_time(d.get("finishedUtc")), error=d.get("error"), bank_name=d.get("bankName"),
            passed=d.get("passed", 0), failed=d.get("failed", 0), errors=d.get("errors", 0),
            blocked=d.get("blocked", 0), skipped=d.get("skipped", 0), pass_rate=d.get("passRate", 0))


class PortalRun:
    """A run as the UI sees it: the persisted record plus live progress while it executes."""

    def __init__(self, record: RunRecord, result: RunResult | None = None):
        self.record = record
        self.result = result
        self.cancel = threading.Event()
        # Scenario being executed right now.
        self.current_scenario: str | None = None
        self.total_scenarios = 0
        # Authorize URL the tester must open (manual SCA) while a step waits for the login.
        self.login_url: str | None = None
        self._lock = threading.Lock()
        self._log: list[str] = []
        self._completed: list[ScenarioResult] = list(result.scenarios) if result else []

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def is_active(self) -> bool:
        return self.record.status in (QUEUED, RUNNING)

    @property
    def log(self) -> list[str]:
        with self._lock:
            return list(self._log)

    @property
    def completed(self) -> list[ScenarioResult]:
        with self._lock:
            return list(self._completed)

    def add_log(self, message: str):
        with self._lock:
            self._log.append(f"{datetime.now():%H:%M:%S}  {message}")

    def add_completed(self, result: ScenarioResult):
        with self._lock:
            self._completed.append(result)


class RunService:
    def __init__(self, catalog: CatalogService, mock_bank: MockBankService, data_directory: str, recorder=None, summaries=None):
        self._catalog = catalog
        self._mock_bank = mock_bank
        # Optional (ai_services): runs and events are also written to PostgreSQL; completed runs can be summarized.
        self._recorder = recorder
        self._summaries = summaries
        self.data_directory = os.path.abspath(data_directory)
        os.makedirs(self.data_directory, exist_ok=True)
        self._runs: dict[str, PortalRun] = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue[PortalRun]" = queue.Queue()
        self._load_history()
        threading.Thread(target=self._worker, daemon=True, name="aqt-portal-runs").start()

    @property
    def runs(self) -> list[PortalRun]:
        with self._lock:
            runs = list(self._runs.values())
        return sorted(runs, key=lambda r: r.record.created_utc or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    def get(self, run_id: str) -> PortalRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def run_directory(self, run_id: str) -> str:
        return os.path.join(self.data_directory, run_id)

    def enqueue(self, request: RunRequest) -> PortalRun:
        profile = self._catalog.load_bank(request.bank)  # validates the bank before queueing
        now = utc_now()
        record = RunRecord(id=f"{now:%Y%m%d-%H%M%S}-{profile.bank.bic}-{uuid.uuid4().hex[:4]}", request=request,
                           status=QUEUED, created_utc=now, bank_name=profile.bank.name)
        run = PortalRun(record)
        with self._lock:
            self._runs[run.id] = run
        self._save(run)
        if self._recorder:
            self._recorder.save_run(run, self.run_directory(run.id))
            self._recorder.event("run.queued", f"{record.bank_name} ({profile.bank.bic}) queued", run.id, data=request.to_dict())
        self._queue.put(run)
        return run

    def cancel(self, run_id: str):
        run = self.get(run_id)
        if run is not None and run.is_active:
            run.cancel.set()

    def delete(self, run_id: str) -> bool:
        run = self.get(run_id)
        if run is None or run.is_active:
            return False
        with self._lock:
            self._runs.pop(run_id, None)
        shutil.rmtree(self.run_directory(run_id), ignore_errors=True)
        if self._recorder:
            self._recorder.delete_run(run_id)
            self._recorder.event("run.deleted", f"Run {run_id} deleted by the user", run_id)
        return True

    def _worker(self):
        while True:
            run = self._queue.get()
            if run.cancel.is_set():
                self._finish(run, CANCELLED, "Cancelled before it started")
                continue
            try:
                self._execute(run)
            except OperationCancelled:
                self._finish(run, CANCELLED, "Cancelled by user")
            except ConfigurationError as ex:
                run.add_log("ERROR: " + str(ex))
                self._finish(run, FAILED, str(ex))
            except Exception as ex:  # noqa: BLE001 - a crashed run is shown, the worker keeps going
                traceback.print_exc()
                run.add_log("ERROR: " + str(ex))
                self._finish(run, FAILED, f"{type(ex).__name__}: {ex}")

    def _execute(self, run: PortalRun):
        request = run.record.request
        run.record.status = RUNNING
        run.record.started_utc = utc_now()
        self._save(run)
        if self._recorder:
            self._recorder.save_run(run, self.run_directory(run.id))
            self._recorder.event("run.started", f"Run started ({request.bank})", run.id)

        profile = self._catalog.load_bank(request.bank)
        all_scenarios = self._catalog.get_scenarios()
        selected = scenarios.filter_scenarios(all_scenarios, request.suites, request.scenario_ids)
        if not selected:
            raise ConfigurationError("No scenarios match the selected suites/scenarios")

        # Same rule as the CLI: with the gate on, connectivity is always checked first.
        if request.preflight_gate and not any(is_preflight(s) for s in selected):
            selected = [s for s in all_scenarios if is_preflight(s)] + selected

        run.total_scenarios = len(selected)
        run.add_log(f"{profile.bank.name} ({profile.bank.bic}) — {profile.endpoints.xs2a} — {len(selected)} scenario(s)")

        if request.use_mock_bank:
            if not CatalogService.is_local(profile):
                raise ConfigurationError("The built-in MockBank can only be used with a localhost profile such as MOCK")
            self._mock_bank.ensure_running()
            run.add_log(f"MockBank is running at {self._mock_bank.xs2a_url}")

        with RunContext.create(profile, self._catalog.get_templates()) as ctx:
            ctx.cancel = run.cancel
            ctx.notify = run.add_log
            if request.auto_sca and CatalogService.allows_headless_sca(profile):
                ctx.open_browser = headless_authorizer(lambda message: run.add_log("ERROR: " + message))
            else:
                # Manual SCA: the tester opens the link from the run page (on the machine the portal runs on).
                def show_login(url, cancel=None):
                    run.login_url = url
                ctx.open_browser = show_login

            if ctx.tpp_id is not None:
                run.add_log(f"TPP id: {ctx.tpp_id}")

            runner = ScenarioRunner(ctx, preflight_gate=request.preflight_gate)
            if request.unattended and not (request.auto_sca and CatalogService.allows_headless_sca(profile)):
                runner.skip_sca_reason = UNATTENDED_SCA_REASON

            def started(s):
                run.current_scenario = f"{s.id} — {s.title}"
                run.login_url = None

            def completed(s):
                run.login_url = None
                run.add_completed(s)
                if self._recorder:
                    self._recorder.event("scenario.completed", f"{s.id} {s.outcome.value}{'' if s.reason is None else ': ' + s.reason}",
                                         run.id, self._recorder.level(s.outcome),
                                         {"scenario": s.id, "outcome": s.outcome.value, "durationMs": s.duration_ms})

            runner.scenario_started.append(started)
            runner.scenario_completed.append(completed)

            result = runner.run(selected)

        run.result = result
        run.current_scenario = None
        reporting.write(result, self.run_directory(run.id), reporting.SUPPORTED_FORMATS)

        self._finish(run, COMPLETED, None)
        run.add_log(f"Finished: {result.count(Outcome.PASSED)} passed, {result.count(Outcome.FAILED)} failed, "
                    f"{result.count(Outcome.ERROR)} errors — pass rate {format_rate(result.pass_rate)}%")

    def _finish(self, run: PortalRun, status: str, error: str | None):
        record = run.record
        record.status = status
        record.error = error
        record.finished_utc = utc_now()
        run.current_scenario = None
        run.login_url = None

        if run.result is not None:
            r = run.result
            record.passed, record.failed, record.errors = r.count(Outcome.PASSED), r.count(Outcome.FAILED), r.count(Outcome.ERROR)
            record.blocked, record.skipped, record.pass_rate = r.count(Outcome.BLOCKED), r.count(Outcome.SKIPPED), r.pass_rate

        self._save(run)
        if self._recorder:
            self._recorder.save_run(run, self.run_directory(run.id))
            ok = status == COMPLETED and record.failed + record.errors == 0
            self._recorder.event(f"run.{status.lower()}", error or f"{record.passed} passed, {record.failed} failed, "
                                 f"{record.errors} errors, {record.skipped} skipped", run.id,
                                 "Info" if ok else ("Error" if status == FAILED else "Warning"))
            self._recorder.run_finished(run.id)
            if status == COMPLETED and self._summaries is not None and self._summaries.auto_summarize:
                summaries = self._summaries
                self._recorder.then(f"auto-summary {run.id}", lambda: summaries.start(run.id, summaries.auto_provider, "auto (portal)"))

    def _save(self, run: PortalRun):
        directory = self.run_directory(run.id)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "run.json"), "w", encoding="utf-8") as f:
            json.dump(run.record.to_dict(), f, indent=2, ensure_ascii=False)

    def _load_history(self):
        for name in os.listdir(self.data_directory):
            directory = os.path.join(self.data_directory, name)
            record_file = os.path.join(directory, "run.json")
            if not os.path.isfile(record_file):
                continue
            try:
                with open(record_file, encoding="utf-8-sig") as f:
                    record = RunRecord.from_dict(json.load(f))

                # A run that was in progress when the portal stopped will never finish.
                if record.status in (QUEUED, RUNNING):
                    record.status = CANCELLED
                    record.error = "The portal stopped while this run was in progress"

                result = None
                summary_file = os.path.join(directory, "summary.json")
                if os.path.isfile(summary_file):
                    with open(summary_file, encoding="utf-8-sig") as f:
                        result = RunResult.from_dict(json.load(f))
                self._runs[record.id] = PortalRun(record, result)
            except (OSError, ValueError, KeyError) as ex:
                print(f"Portal: skipping unreadable run folder {directory}: {ex}")
