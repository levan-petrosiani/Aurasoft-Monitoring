"""Guardrails around the login-less portal and automatic SCA: cross-site posts, DNS rebinding, --sca-auto on real banks."""

import pytest

from aqt.portal.app import create_app

from conftest import REPO_ROOT


@pytest.fixture()
def client(tmp_path):
    return create_app(REPO_ROOT, str(tmp_path), use_database=False).test_client()


def test_forms_posted_from_another_site_are_refused(client):
    form = {"name": "Ana"}
    assert client.post("/reviewer", data=form).status_code == 302                                    # no Origin (curl, tests)
    assert client.post("/reviewer", data=form, headers={"Origin": "http://localhost"}).status_code == 302  # the portal itself
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Referer": "https://evil.example/page"}):
        assert client.post("/reviewer", data=form, headers=headers).status_code == 403, headers
    assert client.post("/mock/stop", headers={"Origin": "https://evil.example"}).status_code == 403


def test_only_local_host_names_are_answered(client):
    assert client.get("/", headers={"Host": "127.0.0.1:5280"}).status_code == 200
    assert client.get("/", headers={"Host": "rebind.evil.example:5280"}).status_code == 403


def test_sca_auto_is_refused_for_a_real_bank_profile(tmp_path, capsys):
    from aqt.cli import CONFIGURATION_ERROR, main
    profile = tmp_path / "REAL.yaml"
    profile.write_text("""
bank: {bic: REALGE22, name: Some real bank}
endpoints: {xs2a: "https://xs2a.realbank.example/"}
""", encoding="utf-8")
    assert main(["run", "--bank", str(profile), "--sca-auto"]) == CONFIGURATION_ERROR
    assert "--sca-auto is only for MockBank and public mocks" in capsys.readouterr().err
