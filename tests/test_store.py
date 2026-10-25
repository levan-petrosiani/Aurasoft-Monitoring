"""Store: masking, mapping runs to rows, importing report folders, and PostgreSQL round trips (with AQT_TEST_DB)."""

import json
import os

import pytest

pytest.importorskip("psycopg")

from aqt import reporting  # noqa: E402
from aqt.store.connection import to_libpq  # noqa: E402
from aqt.store.importer import import_folders, read_folder  # noqa: E402
from aqt.store.mapper import RunInfo, to_rows  # noqa: E402
from aqt.store.masking import MaskingOptions, SensitiveDataMasker, cap  # noqa: E402

from ai_support import ThrowawayDatabase, require_database, sample_run  # noqa: E402

M = SensitiveDataMasker()
INFO = RunInfo(id="TESTGE22-20261024-080000", source="cli", status="Completed", bank_bic="TESTGE22")


# ---- masking -----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,value,expected", [
    ("Authorization", "Bearer eyJhbGciOiJSUzI1NiJ9.payload.sig", "Bearer ***(masked)"),
    ("authorization", "Bearer 2c9e20322e7a…(masked)", "Bearer ***(masked)"),  # the engine's partial mask is masked fully
    ("PSU-ID", "user-12345", "***(masked)"),
    ("X-Request-ID", "6f1c0c1e-0000", "6f1c0c1e-0000"),
])
def test_masks_secret_headers_only(name, value, expected):
    assert M.mask_header(name, value) == expected


def test_masks_tokens_in_json_at_any_depth_and_keeps_other_fields():
    masked = M.mask_body('{"access_token":"abc.def.ghi","token_type":"Bearer","nested":{"refresh_token":"r-123"},"expires_in":3600}')
    assert masked == '{"access_token":"***(masked)","token_type":"Bearer","nested":{"refresh_token":"***(masked)"},"expires_in":3600}'


def test_keeps_xs2a_error_codes_readable():
    masked = M.mask_body('{"code":"FORMAT_ERROR","tppMessages":[{"category":"ERROR","code":"CONSENT_UNKNOWN"}]}')
    assert "FORMAT_ERROR" in masked and "CONSENT_UNKNOWN" in masked


def test_masks_form_bodies_and_query_strings():
    form = M.mask_body("grant_type=authorization_code&code=SplxlOBeZQQYbYS6WxSbIA&code_verifier=dBjftJeZ4CVP&client_id=PSDGE-NBG-X")
    assert "SplxlOBeZQQYbYS6WxSbIA" not in form and "dBjftJeZ4CVP" not in form
    assert "grant_type=authorization_code" in form and "client_id=PSDGE-NBG-X" in form
    url = M.mask_url("http://localhost:5199/callback?code=SplxlOBeZQQYbYS6WxSbIA&state=xyz")
    assert "SplxlOBeZQQYbYS6WxSbIA" not in url and url.endswith("&state=xyz")


def test_masks_ibans_keeping_country_check_digits_and_last_four():
    masked = M.mask_body('{"account":{"iban":"GE29NB0000000101904917"},"bic":"TBCBGE22"}')
    assert "GE29**************4917" in masked and "TBCBGE22" in masked
    assert "GE29NB0000000101904917" in SensitiveDataMasker(MaskingOptions(mask_ibans=False)).mask_body('{"iban":"GE29NB0000000101904917"}')


def test_plain_text_invalid_json_and_cap():
    assert M.mask_body("upstream timeout") == "upstream timeout"
    assert M.mask_body("{not json GE29NB0000000101904917") == "{not json GE29**************4917"
    assert M.mask_body(None) is None
    text, cut = cap("x" * 20, 10)
    assert cut and text.startswith("xxxxxxxxxx…")
    assert cap("short", 10) == ("short", False)


def test_connection_strings_from_the_csharp_tool_still_work():
    assert to_libpq("Host=localhost;Port=5435;Database=aqt;Username=aqt;Password=aqt_dev") == \
        "host=localhost port=5435 dbname=aqt user=aqt password=aqt_dev"
    assert to_libpq("postgresql://aqt@localhost/aqt") == "postgresql://aqt@localhost/aqt"


# ---- mapping and import --------------------------------------------------------------------------------------------

def test_maps_counts_scenarios_steps_cleanup_and_every_exchange():
    row = to_rows(INFO, sample_run(), M, 64 * 1024)
    assert (row.passed, row.failed, row.pass_rate) == (1, 1, 50.0)
    assert [s.scenario_id for s in row.scenarios] == ["AIS-001", "NEG-CON-003"]
    assert len(row.scenarios[0].steps[0].exchanges) == 2  # exchange + exchanges[]
    assert [s.is_cleanup for s in row.scenarios[1].steps] == [False, True]


def test_nothing_secret_survives_the_mapping():
    row = to_rows(INFO, sample_run(), M, 64 * 1024)
    everything = json.dumps([[x.url, x.request_headers, x.request_body, x.response_headers, x.response_body]
                             for s in row.scenarios for st in s.steps for x in st.exchanges])
    assert "SECRET" not in everything and "GE29NB0000000101904917" not in everything


def test_long_bodies_are_capped_and_flagged():
    run = sample_run()
    run.scenarios[1].steps[0].exchange.response_body = "x" * 5000
    x = to_rows(INFO, run, M, 1000).scenarios[1].steps[0].exchanges[0]
    assert x.truncated and len(x.response_body) < 1100


def test_reads_cli_and_portal_folders_including_cancelled_runs(tmp_path):
    cli = tmp_path / "TESTGE22-20261024-080000"
    reporting.write(sample_run(), str(cli), ["json"])
    portal = tmp_path / "portal" / "20261024-080000-TESTGE22-ab12"
    reporting.write(sample_run(), str(portal), ["json"])
    (portal / "run.json").write_text(json.dumps({"id": portal.name, "request": {"bank": "TEST", "suites": ["ais"]}, "status": "Completed",
                                                 "createdUtc": "2026-10-24T08:00:00Z", "bankName": "Test Bank"}))
    cancelled = tmp_path / "portal" / "20261024-090000-BAGAGE22-cd34"
    cancelled.mkdir(parents=True)
    (cancelled / "run.json").write_text(json.dumps({"id": cancelled.name, "request": {"bank": "BAGAGE22"}, "status": "Cancelled",
                                                    "error": "Cancelled before it started", "createdUtc": "2026-10-24T09:00:00Z"}))

    info, result = read_folder(str(cli))
    assert (info.id, info.source, len(result.scenarios)) == ("TESTGE22-20261024-080000", "cli", 2)
    info, _ = read_folder(str(portal))
    assert (info.source, info.status, info.suites, info.options_json["bank"]) == ("portal", "Completed", ["ais"], "TEST")
    info, result = read_folder(str(cancelled))
    assert result is None and (info.status, info.bank_bic) == ("Cancelled", "BAGAGE22")


# ---- PostgreSQL ----------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db():
    require_database()
    database = ThrowawayDatabase()
    yield database
    database.drop()


def test_saves_a_run_and_replaces_it_on_save_again(db):
    info = RunInfo(id="SAVE-1", source="cli", status="Completed", bank_bic="TESTGE22", suites=["ais"])
    db.store.save_run(info, sample_run())
    db.store.save_run(info, sample_run())  # idempotent
    run = db.store.get_run("SAVE-1")
    assert len(run.scenarios) == 2 and run.suites == ["ais"]
    exchanges = [x for s in run.scenarios for st in s.steps for x in st.exchanges]
    assert len(exchanges) == 3  # 2 SCA + 1 request; cleanup has none
    assert all("SECRET" not in (x.request_body or "") + (x.response_body or "") for x in exchanges)
    assert all(v == "Bearer ***(masked)" for x in exchanges for k, v in x.request_headers if k == "Authorization")


def test_delete_cascades_but_events_ai_calls_and_summaries_are_kept(db):
    import uuid
    from datetime import datetime, timezone
    from aqt.store.entities import AiCallRow
    db.store.save_run(RunInfo(id="DEL-1", source="portal", status="Completed", bank_bic="TESTGE22"), sample_run())
    db.store.add_event("portal", "run.finished", "done", "DEL-1", data={"passed": 1})
    db.store.add_ai_call(AiCallRow(id=uuid.uuid4(), time_utc=datetime.now(timezone.utc), feature="check", provider="ollama",
                                   model="qwen3:8b", prompt_sha256="x", request=[{"role": "user", "text": "hi"}], duration_ms=1,
                                   succeeded=True, run_id="DEL-1"))
    assert db.store.delete_run("DEL-1")
    assert db.store.get_run("DEL-1") is None
    with db.store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM events WHERE run_id = 'DEL-1'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM ai_calls WHERE run_id = 'DEL-1'").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM exchanges WHERE run_id = 'DEL-1'").fetchone()[0] == 0


def test_import_skips_existing_runs_unless_replace(db, tmp_path):
    reporting.write(sample_run(), str(tmp_path / "IMPGE22-20261024-080000"), ["json"])
    assert (import_folders(db.store, [str(tmp_path)], False).imported) == 1
    assert (import_folders(db.store, [str(tmp_path)], False).skipped) == 1
    assert (import_folders(db.store, [str(tmp_path)], True).imported) == 1


def test_previous_run_and_latest_run(db):
    from datetime import datetime, timezone
    first = RunInfo(id="PREV-A", source="cli", status="Completed", bank_bic="PRVGE22", created_utc=datetime(2026, 10, 1, tzinfo=timezone.utc))
    second = RunInfo(id="PREV-B", source="cli", status="Completed", bank_bic="PRVGE22", created_utc=datetime(2026, 10, 2, tzinfo=timezone.utc))
    db.store.save_run(first, sample_run())
    db.store.save_run(second, sample_run())
    assert db.store.get_previous_run(db.store.get_run("PREV-B")).id == "PREV-A"
    assert db.store.get_latest_run_id("PRVGE22") == "PREV-B"


def test_clause_search_any_word_stemming_and_meaning(db):
    from aqt.store.entities import ClauseRow, StandardDocumentRow

    def axis(i):
        v = [0.0] * 1024
        v[i] = 1.0
        return v

    clauses = [ClauseRow(standard="SR-IG", version="1", number=n, title="t" + n, text=t, level=1, ord=int(n), language=lang, key=f"SR-IG:1:{n}")
               for n, t, lang in [("1", "The ASPSP rejects consents whose validUntil is in the past.", "en"),
                                  ("2", "Payment initiation with JSON encoding.", "en"),
                                  ("3", "თანხმობის ვადა არ აღემატება 90 დღეს.", "ka")]]
    ids = db.store.replace_standard(StandardDocumentRow(id="SR-IG", version="1", title="Search", language="en", url="u", sha256="x"), clauses)
    db.store.set_clause_embeddings([(i, axis(n)) for n, i in enumerate(ids)])

    by_words = db.store.search_clauses("consent rejected yesterday", None, "SR-IG")
    assert by_words[0].clause.number == "1" and by_words[0].by_words          # stemming: consents ~ consent
    assert [h.clause.number for h in db.store.search_clauses("თანხმობის", None, "SR-IG")] == ["3"]
    assert any(h.clause.number == "3" and h.by_meaning for h in db.store.search_clauses("how long is a consent valid", axis(2), "SR-IG"))
    assert [c.number for c in db.store.get_outline("SR-IG", "1")] == ["1", "2", "3"]


def test_a_database_created_by_the_csharp_tool_is_recognised(db):
    """migrate() on a database that has the C# tool's EF Core history marks 0001 as applied instead of running it,
    then applies the later (Python-only) migrations."""
    from aqt.store.connection import _migration_files, connect, migrate
    with connect(db.connection, register_vector=False) as conn:
        conn.execute("DELETE FROM aqt_schema_migrations")
        # Added by the later migrations; the C# tool never had them.
        conn.execute("DROP TABLE scenario_drafts")
        conn.execute("DROP SCHEMA agent CASCADE")
        conn.execute("DROP TABLE agent_questions, monitor_state, alerts")
        conn.execute('CREATE TABLE "__EFMigrationsHistory" (migration_id varchar(150) PRIMARY KEY, product_version varchar(32))')
        conn.execute("""INSERT INTO "__EFMigrationsHistory" VALUES ('20261024084136_StandardsAndRules', '10.0.13')""")
        conn.commit()
    assert migrate(db.connection) == [v for v, _ in _migration_files() if v != "0001"]
    with connect(db.connection, register_vector=False) as conn:
        assert conn.execute("SELECT note FROM aqt_schema_migrations WHERE version = '0001'").fetchone()[0].startswith("created by the C# tool")
