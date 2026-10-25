"""
Dry runs: one scenario against an in-process MockBank on free localhost ports, never against a real bank. Used to
check scenarios that are not approved yet (AI drafts): the profile is always MOCK and the target always loopback.
"""

import socket

from . import profiles
from .context import RunContext
from .mockbank import MockBankHost, MockBankOptions
from .oauth import headless_authorizer
from .results import ScenarioResult
from .runner import ScenarioRunner
from .scenarios import Scenario, TemplateLibrary


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MockDryRun:
    """Starts MockBank on first use and keeps it for the following dry runs; use as a context manager."""

    def __init__(self, banks_directory: str, scenarios_directory: str):
        self._banks, self._scenarios = banks_directory, scenarios_directory
        self._host: MockBankHost | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        if self._host is not None:
            self._host.stop()
            self._host = None

    def run(self, scenario: Scenario) -> tuple[ScenarioResult, list[str]]:
        """Runs the scenario; returns its result and any errors of the automatic SCA login."""
        if self._host is None:
            host = MockBankHost(MockBankOptions.load({"https_port": _free_port(), "http_port": _free_port()}), quiet=True)
            host.start()
            self._host = host
        profile = profiles.load("MOCK", self._banks)
        base = self._host.options.https_base_url
        profile.endpoints.xs2a = base + "/"
        profile.endpoints.discovery = base + "/.well-known/oauth-authorization-server"
        profile.tpp.redirect_uri = f"http://localhost:{_free_port()}/callback"
        errors: list[str] = []
        ctx = RunContext.create(profile, TemplateLibrary.load(self._scenarios))
        ctx.open_browser = headless_authorizer(errors.append)  # MockBank approves on its own; a real bank never sees this
        with ctx:
            return ScenarioRunner(ctx).run_scenario(scenario), errors
