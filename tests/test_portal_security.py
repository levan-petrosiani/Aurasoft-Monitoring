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


# ---- hosted demo (jury mode, aqt/portal/access.py) --------------------------------------------------------------

def _hosted(tmp_path, **kw):
    from aqt.portal.access import PortalAccess
    access = PortalAccess(hosts=["demo.example.app"], password="s3cret", **kw)
    return create_app(REPO_ROOT, str(tmp_path), use_database=False, access=access).test_client()


def _login(user="jury", password="s3cret"):
    import base64
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


def test_a_public_portal_needs_a_password():
    from aqt.errors import ConfigurationError
    from aqt.portal.access import PortalAccess
    with pytest.raises(ConfigurationError, match="AQT_PORTAL_PASSWORD"):
        PortalAccess(hosts=["demo.example.app"])
    assert PortalAccess().public is False and PortalAccess().check_login(None)


def test_hosted_portal_answers_on_its_name_only_and_asks_for_the_login(tmp_path):
    client = _hosted(tmp_path)
    host = {"Host": "demo.example.app"}
    response = client.get("/", headers=host)
    assert response.status_code == 401 and "Basic" in response.headers["WWW-Authenticate"]
    assert client.get("/", headers={**host, **_login(password="wrong")}).status_code == 401
    page = client.get("/", headers={**host, **_login()})
    assert page.status_code == 200 and "Hosted demo" in page.get_data(as_text=True)
    assert client.get("/", headers={"Host": "other.example.app", **_login()}).status_code == 403
    post = {**host, **_login(), "Origin": "https://demo.example.app"}
    assert client.post("/runs/x/delete", headers=post).status_code == 403            # turned off when hosted
    assert client.post("/reviewer", data={"name": "Ana"}, headers=post).status_code == 302


def test_hosted_portal_caps_model_calls_per_day(tmp_path):
    client = _hosted(tmp_path, ai_per_day=2)
    post = {"Host": "demo.example.app", **_login(), "Origin": "https://demo.example.app"}
    codes = [client.post("/ask", data={"question": "q"}, headers=post).status_code for _ in range(3)]
    assert 429 not in codes[:2] and codes[2] == 429


def test_portal_refuses_to_listen_publicly_without_jury_mode(monkeypatch):
    from aqt.cli import CONFIGURATION_ERROR, main
    for name in ("AQT_PORTAL_HOSTS", "AQT_PORTAL_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert main(["portal", "--host", "0.0.0.0", "--no-db"]) == CONFIGURATION_ERROR
