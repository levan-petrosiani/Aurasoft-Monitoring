"""Shared helpers for the store/standards/AI tests: a throw-away PostgreSQL database, a sample run, scripted models."""

import os
import uuid
from datetime import datetime, timezone

import pytest

from aqt.results import HttpExchange, Outcome, RunResult, ScenarioResult, StepResult

TEST_DB = os.environ.get("AQT_TEST_DB")

needs_ai = pytest.importorskip  # alias for readability in the test modules


def require_database():
    if not TEST_DB:
        pytest.skip("Set AQT_TEST_DB (e.g. host=localhost port=5435 user=aqt password=aqt_dev) to run database tests")
    pytest.importorskip("psycopg")


class ThrowawayDatabase:
    """Creates a database, migrates it, and drops it afterwards."""

    def __init__(self):
        from aqt.store import AqtStore
        from aqt.store.connection import TemporaryDatabase
        self._db = TemporaryDatabase(TEST_DB, "aqt_test_")
        self.name, self.connection = self._db.name, self._db.connection
        self.store = AqtStore(self.connection)

    def drop(self):
        self._db.close()


def exchange(method, url, request_body, status, response_body) -> HttpExchange:
    return HttpExchange(method=method, url=url,
                        request_headers=[("Authorization", "Bearer SECRET-ACCESS-TOKEN"), ("X-Request-ID", "r-1")],
                        request_body=request_body, status_code=status, response_body=response_body, duration_ms=42,
                        started_at_utc=datetime(2026, 10, 24, 8, 0, 1, tzinfo=timezone.utc))


def sample_run() -> RunResult:
    """One passed scenario with an SCA token exchange, one failed with an IBAN in the request."""
    run = RunResult(tool_version="0.1.0", bank_bic="TESTGE22", bank_name="Test Bank", xs2a_url="https://localhost:7443/",
                    tpp_id="PSDGE-NBG-TEST", started_at_utc=datetime(2026, 10, 24, 8, 0, 0, tzinfo=timezone.utc),
                    finished_at_utc=datetime(2026, 10, 24, 8, 0, 5, tzinfo=timezone.utc))
    passed = ScenarioResult(id="AIS-001", title="Account list consent", suite="ais", severity="Critical", spec_ref="6.3.1",
                            outcome=Outcome.PASSED, duration_ms=1200)
    sca = StepResult(name="SCA via OAuth", kind="sca")
    sca.exchange = exchange("POST", "https://localhost:7443/oauth/token", "grant_type=authorization_code&code=SECRETCODE123456789", 200,
                            '{"access_token":"SECRET-ACCESS-TOKEN","refresh_token":"SECRET-REFRESH"}')
    sca.exchanges.append(exchange("POST", "https://localhost:7443/oauth/token", "grant_type=refresh_token&refresh_token=SECRET-REFRESH", 200,
                                  '{"access_token":"SECRET-ACCESS-2"}'))
    passed.steps.append(sca)

    failed = ScenarioResult(id="NEG-CON-003", title="validUntil in the past", suite="negative", severity="Major",
                            outcome=Outcome.FAILED, reason="Expected 400, got 201")
    request = StepResult(name="Create consent", kind="request", outcome=Outcome.FAILED, failures=["Expected status 400 but got 201"])
    request.exchange = exchange("POST", "https://localhost:7443/v2/consents/account-access",
                                '{"access":{"accounts":[{"iban":"GE29NB0000000101904917"}]}}', 201, '{"consentId":"c-1"}')
    failed.steps.append(request)
    failed.cleanup.append(StepResult(name="Delete consent", kind="request", warnings=["cleanup returned 204 slowly"]))
    run.scenarios += [passed, failed]
    return run


class ScriptedChat:
    """A model that replies from a list (or raises an exception given in the list)."""
    provider, model = "fake", "scripted"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def complete(self, system, messages, json_schema=None):
        from aqt.ai.chat import ChatResult
        self.prompts.append((system, messages))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return ChatResult(answer, "scripted", "stop", 10, 5)


def bag_of_words(texts):
    """Deterministic stand-in for bge-m3: equal texts get equal vectors, different ones differ."""
    import zlib
    vectors = []
    for text in texts:
        v = [0.0] * 1024
        for word in text.lower().replace(".", " ").replace(",", " ").split():
            v[zlib.crc32(word.encode()) % 1024] += 1
        vectors.append(v)
    return vectors
