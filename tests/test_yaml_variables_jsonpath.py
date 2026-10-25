import os
import re
from datetime import datetime, timezone

import pytest

from aqt import jsonpath, profiles, yamljson
from aqt.binding import bind
from aqt.errors import ConfigurationError
from aqt.scenarios import Scenario
from aqt.variables import VariableResolver
from conftest import BANKS


def test_plain_scalars_are_typed_quoted_stay_strings():
    doc = yamljson.parse('a: 4\nb: "4"\nc: true\nd: ~\ne: 1.5\nf: 192.168.1.1\ng: \'null\'\nh:\n', "t")
    assert doc == {"a": 4, "b": "4", "c": True, "d": None, "e": 1.5, "f": "192.168.1.1", "g": "null", "h": None}


def test_invalid_yaml_reports_line():
    with pytest.raises(ConfigurationError, match="line 2"):
        yamljson.parse("a: 1\n b: [", "broken.yaml")


def test_binding_rejects_unknown_and_missing_properties():
    with pytest.raises(ConfigurationError, match="unknown property 'titel'"):
        bind(Scenario, {"id": "X", "titel": "t", "suite": "s", "steps": []})
    with pytest.raises(ConfigurationError, match="required property 'title'"):
        bind(Scenario, {"id": "X", "suite": "s", "steps": []})
    with pytest.raises(ConfigurationError, match="expected a string"):
        bind(Scenario, {"id": 5, "title": "t", "suite": "s", "steps": []})


def test_binding_is_case_insensitive():
    s = bind(Scenario, {"ID": "X", "Title": "t", "SUITE": "s", "steps": [{"request": {"path": "p", "BodyTemplate": "b"}}]})
    assert s.steps[0].request.body_template == "b"


def test_jsonpath_subset():
    doc = {"a": [{"b": 1}, {"b": None}], "c": {"d e": "x"}}
    assert jsonpath.select(doc, "$.a[0].b") == [1]
    assert jsonpath.select(doc, "$.a[*].b") == [1, None]
    assert jsonpath.select(doc, "$.c['d e']") == ["x"]
    assert jsonpath.select(doc, "a[5]") == []
    assert jsonpath.exists(doc, "$.a[1].b")  # present null still exists
    assert jsonpath.as_text(None) == "null" and jsonpath.as_text(True) == "true" and jsonpath.as_text({"x": 1}) == '{"x":1}'
    with pytest.raises(ConfigurationError):
        jsonpath.select(doc, "$.a[?(@.b)]")


def test_variables():
    profile = profiles.load("MOCK", BANKS)
    fixed = datetime(2026, 1, 30, 12, 0, 0, tzinfo=timezone.utc)
    v = VariableResolver(profile, "TPP-1", lambda: fixed)
    v.save("consentId", "abc")

    assert v.resolve("{{date.today+2d}}|{{date.today-30d}}|{{date.today}}") == "2026-02-01|2025-12-31|2026-01-30"
    assert v.resolve("{{datetime.now}}") == "2026-01-30T12:00:00Z"
    assert v.resolve("{{bank.bic}} {{tpp.id}} {{ consentId }} {{vars.accounts.gel}}") == "MOCKGE22 TPP-1 abc GE63MK0000000000000001"
    assert re.fullmatch(r"[0-9a-f-]{36}", v.resolve("{{uuid}}"))
    assert v.resolve_node({"a": ["{{consentId}}", 4]}) == {"a": ["abc", 4]}

    with pytest.raises(ConfigurationError, match="Unknown variable"):
        v.resolve("{{nope}}")
    assert v.try_resolve("{{nope}}") is None


def test_dotenv_sets_missing_variables_only(tmp_path, monkeypatch):
    from aqt.cli import load_dotenv
    (tmp_path / ".env").write_text('# secrets\nAQT_T_A=one\nexport AQT_T_B="two words"\nAQT_T_C=keep\nnot a pair\n', encoding="utf-8")
    monkeypatch.setenv("AQT_T_C", "already set")
    monkeypatch.delenv("AQT_T_A", raising=False)
    monkeypatch.delenv("AQT_T_B", raising=False)
    assert load_dotenv(str(tmp_path / ".env")) == ["AQT_T_A", "AQT_T_B"]
    assert (os.environ["AQT_T_A"], os.environ["AQT_T_B"], os.environ["AQT_T_C"]) == ("one", "two words", "already set")
    for name in ("AQT_T_A", "AQT_T_B"):
        monkeypatch.delenv(name)
