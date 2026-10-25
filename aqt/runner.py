import time
from typing import Callable
from urllib.parse import urlsplit

from . import TOOL_VERSION, jsonpath, steps
from .context import RunContext
from .errors import ConfigurationError, OperationCancelled
from .httpclient import RETRIED_HEADER
from .results import Outcome, RunResult, ScenarioResult, StepResult
from .scenarios import Scenario, Step
from .variables import VariableResolver

PREFLIGHT_SUITE = "preflight"


def is_preflight(scenario: Scenario) -> bool:
    return scenario.suite.lower() == PREFLIGHT_SUITE


class ScenarioRunner:
    def __init__(self, ctx: RunContext, preflight_gate: bool = True):
        self._ctx = ctx
        # When true (default), a failed Critical preflight scenario blocks all non-preflight scenarios:
        # if TLS or signing does not work, every other result would be the same failure repeated.
        self.preflight_gate = preflight_gate
        # Called before / after each scenario, for progress output.
        self.scenario_started: list[Callable[[Scenario], None]] = []
        self.scenario_completed: list[Callable[[ScenarioResult], None]] = []
        # Unattended runs (monitoring): scenarios with an SCA step are skipped with this reason, because nobody is
        # there to log in. None (default) runs them. Not set when SCA is automatic (MockBank with --sca-auto).
        self.skip_sca_reason: str | None = None

    def run(self, scenarios: list[Scenario]) -> RunResult:
        # Preflight first, then the rest in their given order (sorted() is stable).
        ordered = sorted(scenarios, key=lambda s: 0 if is_preflight(s) else 1)
        gate_reason = None

        run = RunResult(
            tool_version=TOOL_VERSION,
            bank_bic=self._ctx.profile.bank.bic,
            bank_name=self._ctx.profile.bank.name,
            xs2a_url=self._ctx.profile.endpoints.xs2a,
            tpp_id=self._ctx.tpp_id,
            started_at_utc=self._ctx.now())

        for scenario in ordered:
            self._check_cancel()

            preflight = is_preflight(scenario)
            for handler in self.scenario_started:
                handler(scenario)

            result = (self._blocked(scenario, gate_reason) if gate_reason is not None and not preflight
                      else self.run_scenario(scenario))

            if (self.preflight_gate and preflight and scenario.severity == "Critical"
                    and result.outcome in (Outcome.FAILED, Outcome.ERROR) and gate_reason is None):
                gate_reason = f"Preflight {scenario.id} failed"

            run.scenarios.append(result)
            for handler in self.scenario_completed:
                handler(result)

        run.finished_at_utc = self._ctx.now()
        return run

    def run_scenario(self, scenario: Scenario) -> ScenarioResult:
        result = ScenarioResult(id=scenario.id, title=scenario.title, suite=scenario.suite, severity=scenario.severity,
                                spec_ref=scenario.spec_ref, origin=scenario.origin, source=scenario.source_path)

        missing = next((path for path in scenario.requires or [] if not self._is_satisfied(path)), None)
        if missing is not None:
            result.outcome = Outcome.SKIPPED
            result.reason = f"Profile does not provide '{missing}'"
            return result

        if self.skip_sca_reason is not None and any(st.sca is not None for st in scenario.steps):
            result.outcome = Outcome.SKIPPED
            result.reason = self.skip_sca_reason
            return result

        started = time.perf_counter()
        variables = VariableResolver(self._ctx.profile, self._ctx.tpp_id, self._ctx.now)
        stopped = False

        for i, step in enumerate(scenario.steps):
            if stopped:
                result.steps.append(StepResult(name=step.display_name(i), kind=step.kind, outcome=Outcome.BLOCKED))
                continue

            step_result = self._run_step(step, i, variables)
            result.steps.append(step_result)

            if step_result.outcome in (Outcome.FAILED, Outcome.ERROR):
                stopped = True
                if result.reason is None:
                    first = step_result.failures[0] if step_result.failures else ""
                    result.reason = f"{step_result.name}: {first}"

        for i, step in enumerate(scenario.cleanup or []):
            step_result = self._run_step(step, i, variables)

            # Cleanup problems do not change the verdict, but stay visible.
            if step_result.outcome in (Outcome.FAILED, Outcome.ERROR):
                step_result.warnings.extend("Cleanup: " + f for f in step_result.failures)
                step_result.failures.clear()
                step_result.outcome = Outcome.PASSED

            result.cleanup.append(step_result)

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        if any(s.outcome == Outcome.ERROR for s in result.steps):
            result.outcome = Outcome.ERROR
        elif any(s.outcome == Outcome.FAILED for s in result.steps):
            result.outcome = Outcome.FAILED
        else:
            result.outcome = Outcome.PASSED
            result.reason = None

        return result

    def _run_step(self, step: Step, index: int, variables: VariableResolver) -> StepResult:
        result = StepResult(name=step.display_name(index), kind=step.kind)

        missing = next((name for name in step.run_if or [] if not variables.is_saved(name)), None)
        if missing is not None:
            result.outcome = Outcome.SKIPPED
            result.info.append(f"Skipped: '{missing}' was not saved by an earlier step")
            return result

        started = time.perf_counter()
        try:
            if step.request is not None:
                steps.execute_request(step, self._ctx, variables, result)
            elif step.tls is not None:
                steps.execute_tls(step, self._ctx, result)
            elif step.sca is not None:
                steps.execute_sca(step, self._ctx, variables, result)
            else:
                steps.execute_local_certificates(step, self._ctx, result)

            _report_retries(result)
            result.outcome = (Outcome.ERROR if result.is_tool_error
                              else Outcome.PASSED if not result.failures
                              else Outcome.FAILED)
        except OperationCancelled:
            raise
        except ConfigurationError as ex:
            result.failures.append(str(ex))
            result.outcome = Outcome.ERROR
        except Exception as ex:  # noqa: BLE001 - any crash becomes a visible Error, like in the C# runner
            result.failures.append(f"Unexpected error: {type(ex).__name__}: {ex}")
            result.outcome = Outcome.ERROR

        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    def _check_cancel(self):
        if self._ctx.cancel.is_set():
            raise OperationCancelled()

    @staticmethod
    def _blocked(scenario: Scenario, reason: str) -> ScenarioResult:
        return ScenarioResult(id=scenario.id, title=scenario.title, suite=scenario.suite, severity=scenario.severity,
                              spec_ref=scenario.spec_ref, origin=scenario.origin, source=scenario.source_path,
                              outcome=Outcome.BLOCKED, reason=reason)

    def _is_satisfied(self, requirement: str) -> bool:
        """
        A requires-path is satisfied when it resolves in the profile to something other than null, false or an
        empty string/array/object. path=value instead requires the value itself, or an array containing it
        (e.g. features.consents=aspspManaged).
        """
        raw = self._ctx.profile.raw
        eq = requirement.find("=")
        if eq > 0:
            expected = requirement[eq + 1:].strip().lower()
            found = jsonpath.select(raw, requirement[:eq].strip())
            if not found:
                return False
            if isinstance(found[0], list):
                return any(jsonpath.as_text(item).lower() == expected for item in found[0])
            return jsonpath.as_text(found[0]).lower() == expected

        matches = jsonpath.select(raw, requirement)
        if not matches:
            return False
        value = matches[0]
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, dict)):
            return len(value) > 0
        return True


def _report_retries(step: StepResult):
    """Transient failures that the client retried stay visible as warnings."""
    for x in step.all_exchanges():
        retried = next((v for k, v in x.response_headers if k.lower() == RETRIED_HEADER.lower()), None)
        if retried is not None:
            step.warnings.append(f"{x.method} {urlsplit(x.url).path}: transient failure ({retried}), succeeded after retry")
