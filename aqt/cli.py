"""aqt command line: run, mock, list, validate, version, help — and db, ai, standards, rules (aqt/cli_ai.py, which needs
the `ai` extra). Same commands, options and exit codes as the C# tool."""

import os
import signal
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

from . import TOOL_VERSION, profiles, reporting, scenarios
from .context import RunContext
from .errors import ConfigurationError, OperationCancelled
from .oauth import headless_authorizer, is_loopback_host
from .results import Outcome, RunResult, ScenarioResult, format_rate
from .runner import ScenarioRunner, is_preflight
from .scenarios import TemplateLibrary

SUCCESS = 0
TESTS_FAILED = 1
CONFIGURATION_ERROR = 2

_FLAGS = {"verbose", "no-preflight-gate", "sca-auto", "with-mock", "replace", "show-input", "no-embed", "no-db", "auto-summarize", "drafts"}

# Folder that holds banks/ and scenarios/ next to this Python package (the repository root).
_TOOL_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# ---- console output --------------------------------------------------------------------------------------------

class Out:
    """Coloured console output (ANSI colours only when writing to a terminal)."""

    _COLOURS = {"white": "97", "muted": "90", "green": "32", "red": "31", "magenta": "35", "yellow": "33",
                "dark_yellow": "33", "cyan": "36", "gray": "37"}

    @staticmethod
    def _write(text: str, colour: str, stream=None):
        stream = stream or sys.stdout
        if stream.isatty():
            text = f"\x1b[{Out._COLOURS[colour]}m{text}\x1b[0m"
        print(text, file=stream, flush=True)

    @staticmethod
    def header(text):
        Out._write(text, "white")

    @staticmethod
    def muted(text):
        Out._write(text, "muted")

    @staticmethod
    def ok(text):
        Out._write("  OK    " + text, "green")

    @staticmethod
    def notice(text):
        """Something the tester has to act on now (e.g. log in for SCA)."""
        Out._write("  >>    " + text, "cyan")

    @staticmethod
    def error(text):
        Out._write("ERROR: " + text, "red", sys.stderr)

    @staticmethod
    def scenario(s: ScenarioResult, verbose: bool):
        label, colour = {
            Outcome.PASSED: ("PASS ", "green"),
            Outcome.FAILED: ("FAIL ", "red"),
            Outcome.ERROR: ("ERROR", "magenta"),
            Outcome.SKIPPED: ("SKIP ", "muted"),
            Outcome.BLOCKED: ("BLOCK", "dark_yellow"),
        }.get(s.outcome, (str(s.outcome).upper(), "gray"))

        warnings = len(s.all_warnings)
        suffix = f", {warnings} warning(s)" if warnings else ""
        Out._write(f"  {label}  {s.id:<14} {s.title}  ({s.duration_ms} ms{suffix})", colour)

        if s.outcome != Outcome.PASSED and s.reason is not None:
            Out._write(f"         {s.reason}", "muted")

        if not verbose:
            return

        for step in s.steps + s.cleanup:
            Out._write(f"         - {step.name}: {step.outcome}", "gray")
            for f in step.failures:
                Out._write(f"             x {f}", "red")
            for w in step.warnings:
                Out._write(f"             ! {w}", "yellow")
            for i in step.info:
                Out._write(f"             - {i}", "muted")

    @staticmethod
    def summary(run: RunResult):
        bad = run.count(Outcome.FAILED) + run.count(Outcome.ERROR)
        Out._write(f"Passed {run.count(Outcome.PASSED)}, failed {run.count(Outcome.FAILED)}, errors {run.count(Outcome.ERROR)}, "
                   f"blocked {run.count(Outcome.BLOCKED)}, skipped {run.count(Outcome.SKIPPED)} - pass rate {format_rate(run.pass_rate)}%",
                   "green" if bad == 0 else "red")


# ---- arguments -------------------------------------------------------------------------------------------------

class CliArgs:
    """Minimal argument parser: command [positional...] [--name value | --name=value | --flag]."""

    def __init__(self):
        self.command = ""
        self.positional: list[str] = []
        self._options: dict[str, str | None] = {}

    @staticmethod
    def parse(args: list[str]) -> "CliArgs":
        result = CliArgs()
        i = 0
        while i < len(args):
            arg = args[i]
            is_command_alias = result.command == "" and arg in ("--help", "--version")
            if arg.startswith("--") and len(arg) > 2 and not is_command_alias:
                name, value = arg[2:], None
                if "=" in name:
                    name, value = name.split("=", 1)
                elif name.lower() not in _FLAGS:
                    if i + 1 >= len(args) or args[i + 1].startswith("--"):
                        raise ConfigurationError(f"Option --{name} needs a value")
                    i += 1
                    value = args[i]
                result._options[name.lower()] = value
            elif result.command == "":
                result.command = arg.lower()
            else:
                result.positional.append(arg)
            i += 1
        return result

    def has(self, name: str) -> bool:
        return name.lower() in self._options

    def get(self, name: str) -> str | None:
        return self._options.get(name.lower())

    def get_list(self, name: str) -> list[str]:
        """Comma-separated option value as a list; empty when the option is absent."""
        value = self.get(name)
        return [v.strip() for v in value.split(",") if v.strip()] if value else []


class Paths:
    def __init__(self, cli: CliArgs):
        root = cli.get("root") or os.environ.get("AQT_HOME") or self._find_root()
        self.root = root
        self.banks = cli.get("banks") or os.path.join(root, "banks")
        self.scenarios = cli.get("scenarios") or os.path.join(root, "scenarios")

    @staticmethod
    def _find_root() -> str:
        cwd = os.getcwd()
        return cwd if os.path.isdir(os.path.join(cwd, "scenarios")) else _TOOL_ROOT


# ---- helpers ---------------------------------------------------------------------------------------------------

def is_listening(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_bank(xs2a_url: str, seconds: int, cancel: threading.Event):
    """--wait: lets the CLI start together with the bank (e.g. a multi-project launch in the IDE)."""
    parts = urlsplit(xs2a_url)
    host, port = parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
    deadline = time.monotonic() + seconds
    announced = False
    while not is_listening(host, port, 2):
        if cancel.is_set():
            raise OperationCancelled()
        if time.monotonic() > deadline:
            raise ConfigurationError(f"{host}:{port} did not accept connections within {seconds} s. Is the bank (or MockBank) running?")
        if not announced:
            Out.muted(f"Waiting up to {seconds} s for {host}:{port} ...")
            announced = True
        time.sleep(0.5)


def start_embedded_mock(xs2a_url: str):
    """--with-mock: runs MockBank inside this process, unless one is already listening. Returns the host or None."""
    from .mockbank import MockBankHost, MockBankOptions

    parts = urlsplit(xs2a_url)
    if not is_loopback_host(parts.hostname):
        raise ConfigurationError(f"--with-mock only works with a localhost profile such as MOCK; this profile points to {parts.hostname}")

    port = parts.port or 443
    if is_listening(parts.hostname, port):
        Out.muted(f"MockBank already running on {parts.hostname}:{port}; using it")
        return None

    host = MockBankHost(MockBankOptions.load({"https_port": port}), quiet=True)
    try:
        host.start()
    except OSError as ex:
        raise ConfigurationError(f"Cannot start MockBank on port {port}: {ex}") from ex
    Out.muted(f"Started MockBank in this process: XS2A https://{parts.netloc}, dashboard {host.options.http_base_url}/")
    return host


# ---- commands --------------------------------------------------------------------------------------------------

def cmd_run(cli: CliArgs, cancel: threading.Event) -> int:
    bank = cli.get("bank")
    if not bank:
        raise ConfigurationError("Missing --bank <BIC or profile path>. Use 'aqt list banks' to see profiles.")
    paths = Paths(cli)

    profile = profiles.load(bank, paths.banks)
    templates = TemplateLibrary.load(paths.scenarios)
    all_scenarios = scenarios.load_all(paths.scenarios)
    selected = scenarios.filter_scenarios(all_scenarios, cli.get_list("suite"), cli.get_list("scenario"))
    if not selected:
        raise ConfigurationError("No scenarios match the given --suite/--scenario filter")

    # With the gate on, a filtered run still checks connectivity first, so a bank that is down fails once, clearly.
    gate = not cli.has("no-preflight-gate")
    if gate and not any(is_preflight(s) for s in selected):
        selected = [s for s in all_scenarios if is_preflight(s)] + selected

    formats = cli.get_list("report") or ["html", "json", "junit"]

    Out.header(f"ApiTestTool {TOOL_VERSION} (Python) - {profile.bank.name} ({profile.bank.bic})")
    Out.muted(f"XS2A: {profile.endpoints.xs2a}")
    Out.muted(f"Scenarios: {len(selected)} from {os.path.abspath(paths.scenarios)}")
    print()

    mock = start_embedded_mock(profile.endpoints.xs2a) if cli.has("with-mock") else None
    try:
        wait = cli.get("wait")
        if wait is not None:
            try:
                seconds = int(wait)
            except ValueError as ex:
                raise ConfigurationError(f"--wait needs a number of seconds, got '{wait}'") from ex
            wait_for_bank(profile.endpoints.xs2a, seconds, cancel)

        with RunContext.create(profile, templates) as ctx:
            ctx.cancel = cancel
            ctx.notify = Out.notice
            if cli.has("sca-auto"):
                ctx.open_browser = headless_authorizer(Out.error)
                ctx.notify = lambda message: Out.muted("         (sca-auto) " + message)  # nothing for the tester to do
            if ctx.tpp_id is not None:
                Out.muted(f"TPP id: {ctx.tpp_id}")

            runner = ScenarioRunner(ctx, preflight_gate=gate)
            verbose = cli.has("verbose")
            runner.scenario_completed.append(lambda s: Out.scenario(s, verbose))

            run = runner.run(selected)
    finally:
        if mock is not None:
            mock.stop()

    out_dir = cli.get("out") or os.path.join(os.getcwd(), "reports")
    run_id = f"{profile.bank.bic}-{run.started_at_utc:%Y%m%d-%H%M%S}"
    run_dir = os.path.join(out_dir, run_id)
    files = reporting.write(run, run_dir, formats)

    print()
    Out.summary(run)
    for f in files:
        Out.muted(f"  {f}")

    # Optional PostgreSQL store: only with --db or AQT_DB; a store problem is a notice, never a failed run.
    db = cli.get("db") or os.environ.get("AQT_DB")
    if db:
        from . import cli_ai
        cli_ai.save_run(db, run_id, run_dir, run, cli.get_list("suite"))

    return TESTS_FAILED if run.count(Outcome.FAILED) + run.count(Outcome.ERROR) > 0 else SUCCESS


def cmd_list(cli: CliArgs) -> int:
    paths = Paths(cli)
    what = cli.positional[0] if cli.positional else "scenarios"

    if what == "banks":
        for file in profiles.list_profiles(paths.banks):
            try:
                p = profiles.load_file(file)
                print(f"{p.bank.bic:<12} {p.bank.name:<28} {p.endpoints.xs2a}")
            except ConfigurationError as ex:
                Out.error(str(ex))
        return SUCCESS

    if what == "scenarios":
        selected = scenarios.filter_scenarios(scenarios.load_all(paths.scenarios), cli.get_list("suite"), [])
        for s in sorted(selected, key=lambda s: (s.suite, s.id)):
            print(f"{s.suite:<10} {s.id:<14} {s.severity:<9} {s.title}")
        Out.muted(f"{len(selected)} scenario(s)")
        return SUCCESS

    raise ConfigurationError("Usage: aqt list banks|scenarios [--suite name]")


def cmd_validate(cli: CliArgs) -> int:
    paths = Paths(cli)
    ok = True

    for file in profiles.list_profiles(paths.banks):
        try:
            profiles.load_file(file)
            Out.ok(f"profile   {os.path.basename(file)}")
        except ConfigurationError as ex:
            ok = False
            Out.error(str(ex))

    try:
        templates = TemplateLibrary.load(paths.scenarios)
        Out.ok(f"templates {len(templates.header_set_names)} header set(s), {len(templates.body_names)} bod(ies)")

        loaded = scenarios.load_all(paths.scenarios)
        for s in loaded:
            scenarios.check_templates(s, templates)

        Out.ok(f"scenarios {len(loaded)} valid")
        _warn_rule_links(cli, loaded)
    except ConfigurationError as ex:
        ok = False
        Out.error(str(ex))

    return SUCCESS if ok else CONFIGURATION_ERROR


def _warn_rule_links(cli: CliArgs, loaded) -> None:
    """With --db or AQT_DB: warn about scenarios whose `rules:` name a rule that is missing or not approved."""
    if not any(s.rules for s in loaded):
        return
    try:
        from . import cli_ai
        connection = cli_ai.run_connection(cli)
        if connection is None:
            Out.muted("rules     not checked against the store (set --db or AQT_DB)")
            return
        from .store import AqtStore
        from .store.coverage import scenario_rule_problems
        problems = scenario_rule_problems(AqtStore(connection), loaded)
    except Exception as ex:  # the store is optional: a failure is a notice, never a failed validate
        Out.muted(f"rules     not checked against the store: {type(ex).__name__}: {ex}")
        return
    for p in problems:
        Out._write("  WARN  " + p, "dark_yellow")
    if not problems:
        Out.ok("rules     every scenario rule link is an approved rule")


def cmd_mock(cancel: threading.Event) -> int:
    """aqt mock: runs MockBank in the foreground until Ctrl+C."""
    from .mockbank import MockBankHost

    host = MockBankHost()
    try:
        host.start()
    except OSError as ex:
        raise ConfigurationError(f"Cannot start MockBank: {ex}") from ex
    Out.muted("Press Ctrl+C to stop MockBank.")
    try:
        while not cancel.wait(0.5):
            pass
    finally:
        host.stop()
    return SUCCESS


def _ai_usage() -> str:
    from .cli_ai import USAGE
    return USAGE


def cmd_help() -> int:
    print(f"""ApiTestTool {TOOL_VERSION} (Python) - tests a bank's XS2A sandbox as a TPP

Usage:
  aqt run --bank <BIC|profile.yaml> [--suite a,b] [--scenario ID,ID] [--report html,json,junit] [--out dir] [--verbose] [--no-preflight-gate]
          [--sca-auto] [--wait <seconds>] [--with-mock]
  aqt mock                     run MockBank (a fake bank) until Ctrl+C
  aqt portal [--port 5280] [--db conn | --no-db] [--auto-summarize]   run the web portal (store/AI pages need the database)
  aqt list banks|scenarios [--suite name]
  aqt validate
  aqt version

Store and AI (pip install -e '.[ai]'; PostgreSQL via docker compose up -d):
{_ai_usage()}

Common options:
  --root <dir>        folder containing banks/ and scenarios/ (default: current folder, then the tool folder; env AQT_HOME)
  --banks <dir>       override the bank profiles folder
  --scenarios <dir>   override the scenarios folder
  --db <connection>   PostgreSQL store (env AQT_DB); 'run' saves its results there when set

Testing without a bank:
  aqt run --bank MOCK --with-mock --sca-auto
  --with-mock         start MockBank inside aqt for this run (or reuse one already running)
  --sca-auto          follow the OAuth login redirect without a browser (auto-approving servers such as MockBank only)
  --wait <seconds>    wait until the bank's XS2A port accepts connections before starting

Exit codes: 0 all passed, 1 failures or errors in the run, 2 invalid input or configuration""")
    return SUCCESS


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if os.name == "nt":
        os.system("")  # enables ANSI colours in the Windows console

    cancel = threading.Event()

    def on_sigint(signum, frame):
        if cancel.is_set():
            raise KeyboardInterrupt
        cancel.set()

    signal.signal(signal.SIGINT, on_sigint)

    try:
        cli = CliArgs.parse(argv)
        command = cli.command
        if command == "run":
            return cmd_run(cli, cancel)
        if command == "mock":
            return cmd_mock(cancel)
        if command == "portal":
            from .portal.app import run_portal
            return run_portal(cli)
        if command in ("db", "ai", "standards", "rules", "scenarios", "coverage", "ask"):
            from . import cli_ai
            return {"db": cli_ai.cmd_db, "ai": cli_ai.cmd_ai, "standards": cli_ai.cmd_standards, "rules": cli_ai.cmd_rules,
                    "scenarios": cli_ai.cmd_scenarios, "coverage": cli_ai.cmd_coverage, "ask": cli_ai.cmd_ask}[command](cli)
        if command == "list":
            return cmd_list(cli)
        if command == "validate":
            return cmd_validate(cli)
        if command in ("version", "--version"):
            print(TOOL_VERSION)
            return SUCCESS
        if command in ("help", "--help", "-h", ""):
            return cmd_help()
        Out.error(f"Unknown command '{command}'")
        cmd_help()
        return CONFIGURATION_ERROR
    except ConfigurationError as ex:
        Out.error(str(ex))
        return CONFIGURATION_ERROR
    except (OperationCancelled, KeyboardInterrupt):
        Out.error("Cancelled")
        return CONFIGURATION_ERROR


def _entry():
    """Console script `aqt`."""
    sys.exit(main(sys.argv[1:]))
