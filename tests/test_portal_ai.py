"""Portal pages of the store/AI layer (Flask test client against a throw-away database)."""

import json

import pytest

pytest.importorskip("psycopg")

from aqt.portal.app import create_app  # noqa: E402
from aqt.store.entities import ClauseRow, StandardDocumentRow  # noqa: E402

from ai_support import ThrowawayDatabase, bag_of_words, require_database  # noqa: E402
from conftest import REPO_ROOT  # noqa: E402


def test_without_a_database_the_pages_explain_how_to_turn_them_on(tmp_path):
    app = create_app(REPO_ROOT, str(tmp_path), use_database=False)
    client = app.test_client()
    for page in ("/rules", "/standards", "/drafts", "/coverage"):
        response = client.get(page)
        assert response.status_code == 200 and b"--no-db" in response.data
    assert client.post("/rules/X/approve").status_code == 404


@pytest.fixture(scope="module")
def portal(tmp_path_factory):
    require_database()
    db = ThrowawayDatabase()
    db.store.replace_standard(
        StandardDocumentRow(id="PT-IG", version="1.0", title="Portal test", language="en", url="u", sha256="x"),
        [ClauseRow(standard="PT-IG", version="1.0", number="9.1", title="Consent term", level=1, ord=0, language="en", key="PT-IG:1.0:9.1",
                   text="If the value exceeds 90 days from the request, the ASPSP shall return the error PERIOD_INVALID.")])
    from aqt.ai import options as opt
    from aqt.ai.chat import ChatFactory, StoreAuditSink
    from aqt.ai.rules import RuleDrafter

    class Model:
        provider, model = "fake", "scripted"

        def complete(self, system, messages, json_schema=None):
            from aqt.ai.chat import ChatResult
            return ChatResult(json.dumps({"skipped": "", "rules": [{
                "title": "Reject long consents", "level": "MUST", "service": "AIS", "area": "CON",
                "requirement": "The ASPSP rejects validUntil beyond 90 days with PERIOD_INVALID.",
                "quote": "the ASPSP shall return the error PERIOD_INVALID", "quoteEnglish": "", "operation": "", "notes": "",
                "check": {"kind": "negative", "description": "d", "expectStatus": [400], "expectErrorCodes": ["PERIOD_INVALID"]}}]}),
                "scripted", "stop")

    factory = ChatFactory(opt.AiOptions(), StoreAuditSink(db.store), db.store.masker, provider_override=lambda s: Model(), embed_override=bag_of_words)
    rule = RuleDrafter(db.store, factory).draft("PT-IG:1.0:9.1").rules[0]
    app = create_app(REPO_ROOT, str(tmp_path_factory.mktemp("runs")), database=db.connection)
    yield app, db, rule
    db.drop()


def test_pages_render_with_data(portal):
    app, _, rule = portal
    client = app.test_client()
    page = client.get("/rules")
    assert page.status_code == 200 and rule.id.encode() in page.data and b"How to review" in page.data
    page = client.get("/standards?clause=PT-IG%3A1.0%3A9.1")
    assert page.status_code == 200 and b"Consent term" in page.data and rule.id.encode() in page.data


def test_review_needs_a_name_and_records_it(portal):
    app, db, rule = portal
    client = app.test_client()
    response = client.post(f"/rules/{rule.id}/approve", data={"back": "/rules"})
    assert response.status_code == 302 and "error=" in response.headers["Location"]
    assert db.store.get_rule(rule.id).status == "draft"

    client.post("/reviewer", data={"name": "Ana", "next": "/rules"})
    response = client.post(f"/rules/{rule.id}/edit", data={"back": f"/rules?edit={rule.id}", "title": "Reject consents over 90 days",
                                                           "level": "MUST", "service": "AIS", "area": "CON",
                                                           "requirement": rule.requirement, "quote": rule.quote,
                                                           "expect_status": "400", "expect_codes": "PERIOD_INVALID"})
    assert "edit=" not in response.headers["Location"]
    client.post(f"/rules/{rule.id}/approve", data={"back": "/rules"})
    saved = db.store.get_rule(rule.id)
    assert (saved.status, saved.reviewed_by, saved.title) == ("approved", "Ana", "Reject consents over 90 days")
    assert [h.action for h in db.store.get_rule_history(rule.id)] == ["drafted", "edited", "approved"]


def test_redirects_stay_on_the_portal(portal):
    app, _, _ = portal
    response = app.test_client().post("/reviewer", data={"name": "Ana", "next": "https://evil.example/"})
    assert response.headers["Location"] == "/rules"


def test_drafts_and_coverage_pages(portal):
    import uuid
    from datetime import datetime, timezone
    from aqt.store.entities import ScenarioDraftRow
    app, db, rule = portal
    client = app.test_client()
    client.post("/reviewer", data={"name": "Ana", "next": "/drafts"})
    if db.store.get_rule(rule.id).status != "approved":
        client.post(f"/rules/{rule.id}/approve", data={"back": "/rules"})
    now = datetime.now(timezone.utc)
    draft = ScenarioDraftRow(id=uuid.uuid4(), rule_id=rule.id, scenario_id="NEG-CON-900", suite="negative", title="Long consent rejected",
                             yaml="id: NEG-CON-900\n", status="draft", attempts=2, drafted_by="test", created_utc=now, updated_utc=now,
                             check_notes=["Attempt 1: Failed on MockBank: Status 201, expected 400"], dry_run_outcome="Failed",
                             dry_run={"outcome": "Failed", "reason": "r", "steps": [{"name": "POST consent", "outcome": "Failed", "failures": ["Status 201, expected 400"],
                                                                                      "request": "POST https://localhost/v2/consents", "status": 201}], "cleanup": [], "scaErrors": []},
                             mock_gap="MockBank does not check the 90-day limit")
    db.store.add_scenario_draft(draft)

    page = client.get("/drafts")
    assert page.status_code == 200 and b"NEG-CON-900" in page.data and b"MockBank gap" in page.data and b"Status 201, expected 400" in page.data
    response = client.post(f"/drafts/{draft.id}/reject", data={"back": "/drafts"})
    assert "error=" in response.headers["Location"] and db.store.get_scenario_draft(draft.id).status == "draft"
    client.post(f"/drafts/{draft.id}/reject", data={"back": "/drafts", "note": "Tests the wrong field"})
    assert (db.store.get_scenario_draft(draft.id).status, db.store.get_scenario_draft(draft.id).reviewed_by) == ("rejected", "Ana")

    page = client.get("/coverage")
    assert page.status_code == 200 and rule.id.encode() in page.data and b"No scenario yet" in page.data
