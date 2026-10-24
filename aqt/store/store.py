"""
Writes and reads runs, events, AI calls, summaries, standards, clauses and rules in PostgreSQL.

Every method opens its own short-lived connection, so one AqtStore can be shared by threads (portal) and created per
command (CLI). All run data is masked before it is written.
"""

from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from uuid import uuid4

from . import connection as conn_mod
from .entities import (AgentQuestionRow, AiCallRow, AlertRow, MonitorStateRow, ClauseRow, ExchangeRow, RuleReviewRow, RuleRow, RunRow, RunSummaryRow, ScenarioDraftRow,
                       ScenarioRow, StandardDocumentRow, StepRow)
from .mapper import RunInfo, to_rows
from .masking import MaskingOptions, SensitiveDataMasker


@dataclass
class StoreOptions:
    # Request/response bodies longer than this are cut before they are stored.
    max_body_chars: int = 64 * 1024
    masking: MaskingOptions = field(default_factory=MaskingOptions)


@dataclass
class StoreStatus:
    runs: int
    scenarios: int
    exchanges: int
    events: int
    ai_calls: int
    pending_migrations: list[str]


@dataclass
class ClauseHit:
    """A clause found by search_clauses, with how it matched."""
    clause: ClauseRow
    score: float
    by_words: bool
    by_meaning: bool


# Child lists that are other tables, not columns.
_CHILDREN = {RunRow: ("scenarios",), ScenarioRow: ("steps",), StepRow: ("exchanges",)}


def _columns(cls, exclude=()) -> list[str]:
    skip = set(_CHILDREN.get(cls, ())) | set(exclude)
    return [f.name for f in fields(cls) if f.name not in skip]


# Embeddings are written but not read back (search happens in SQL).
_CLAUSE_COLUMNS = ", ".join(_columns(ClauseRow, exclude=("embedding",)))
_RULE_COLUMNS = ", ".join(_columns(RuleRow, exclude=("embedding",)))


def _vector_literal(v) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def any_word(query: str) -> str:
    """websearch_to_tsquery needs every word by default, which is too strict for questions; join the words with "or"
    (ranking still prefers clauses with more of them). Quoted phrases and an explicit "or" keep their meaning."""
    if '"' in query or " or " in query.lower():
        return query
    return " or ".join(w for w in query.split() if w)


class AqtStore:
    def __init__(self, connection: str, options: StoreOptions | None = None):
        self.connection = connection
        self.options = options or StoreOptions()
        self.masker = SensitiveDataMasker(self.options.masking)

    def connect(self):
        return conn_mod.connect(self.connection)

    def _cursor(self, conn, row_cls=None):
        if row_cls is None:
            return conn.cursor()
        from psycopg.rows import class_row
        return conn.cursor(row_factory=class_row(row_cls))

    @staticmethod
    def _jsonb(value):
        from psycopg.types.json import Jsonb
        return None if value is None else Jsonb(value)

    # ---- schema ------------------------------------------------------------------------------------------------

    def migrate(self) -> list[str]:
        return conn_mod.migrate(self.connection)

    def status(self) -> StoreStatus:
        pending = conn_mod.pending_migrations(self.connection)
        if "0001" in pending:
            return StoreStatus(0, 0, 0, 0, 0, pending)
        with self.connect() as conn, conn.cursor() as cur:
            counts = []
            for table in ("runs", "scenario_results", "exchanges", "events", "ai_calls"):
                cur.execute(f"SELECT count(*) FROM {table}")
                counts.append(cur.fetchone()[0])
        return StoreStatus(*counts, pending)

    # ---- runs --------------------------------------------------------------------------------------------------

    def save_run(self, info: RunInfo, result) -> None:
        """Inserts or replaces a run with all its scenario/step/exchange rows (one transaction)."""
        run = to_rows(info, result, self.masker, self.options.max_body_chars)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM runs WHERE id = %s", (run.id,))
                cols = _columns(RunRow)
                values = [self._jsonb(run.options_json) if c == "options_json" else getattr(run, c) for c in cols]
                cur.execute(f"INSERT INTO runs ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", values)
                for s in run.scenarios:
                    cur.execute(
                        """INSERT INTO scenario_results (run_id, ordinal, scenario_id, title, suite, severity, spec_ref, origin, outcome, reason, duration_ms)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (s.run_id, s.ordinal, s.scenario_id, s.title, s.suite, s.severity, s.spec_ref, s.origin, s.outcome, s.reason, s.duration_ms))
                    scenario_id = cur.fetchone()[0]
                    for st in s.steps:
                        cur.execute(
                            """INSERT INTO step_results (scenario_result_id, run_id, ordinal, is_cleanup, name, kind, outcome, failures, warnings, info, duration_ms)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                            (scenario_id, st.run_id, st.ordinal, st.is_cleanup, st.name, st.kind, st.outcome, st.failures, st.warnings, st.info, st.duration_ms))
                        step_id = cur.fetchone()[0]
                        for x in st.exchanges:
                            cur.execute(
                                """INSERT INTO exchanges (step_result_id, run_id, ordinal, method, url, status_code, duration_ms, error, started_utc,
                                                          request_headers, request_body, response_headers, response_body, truncated)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                                (step_id, x.run_id, x.ordinal, x.method, x.url, x.status_code, x.duration_ms, x.error, x.started_utc,
                                 self._jsonb(x.request_headers), x.request_body, self._jsonb(x.response_headers), x.response_body, x.truncated))
            conn.commit()

    def run_exists(self, run_id: str) -> bool:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM runs WHERE id = %s", (run_id,))
            return cur.fetchone() is not None

    def delete_run(self, run_id: str) -> bool:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM runs WHERE id = %s", (run_id,))
                deleted = cur.rowcount > 0
            conn.commit()
        return deleted

    def add_event(self, source: str, type_: str, message: str, run_id: str | None = None, level: str = "Info", data=None) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO events (time_utc, run_id, source, type, level, message, data) VALUES (now(), %s, %s, %s, %s, %s, %s)",
                            (run_id, source, type_, level, message, self._jsonb(data)))
            conn.commit()

    def add_ai_call(self, call: AiCallRow) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cols = _columns(AiCallRow)
                values = [self._jsonb(call.request) if c == "request" else getattr(call, c) for c in cols]
                cur.execute(f"INSERT INTO ai_calls ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", values)
            conn.commit()

    def get_run(self, run_id: str) -> RunRow | None:
        """A run with scenarios, steps and exchanges, ordered as executed."""
        with self.connect() as conn:
            with self._cursor(conn, RunRow) as cur:
                cur.execute(f"SELECT {', '.join(_columns(RunRow))} FROM runs WHERE id = %s", (run_id,))
                run = cur.fetchone()
            if run is None:
                return None
            with self._cursor(conn, ScenarioRow) as cur:
                cur.execute(f"SELECT {', '.join(_columns(ScenarioRow))} FROM scenario_results WHERE run_id = %s ORDER BY ordinal", (run_id,))
                run.scenarios = cur.fetchall()
            with self._cursor(conn, StepRow) as cur:
                cur.execute(f"SELECT {', '.join(_columns(StepRow))} FROM step_results WHERE run_id = %s ORDER BY ordinal", (run_id,))
                steps = cur.fetchall()
            with self._cursor(conn, ExchangeRow) as cur:
                cur.execute(f"SELECT {', '.join(_columns(ExchangeRow))} FROM exchanges WHERE run_id = %s ORDER BY ordinal", (run_id,))
                exchanges = cur.fetchall()
        by_step: dict[int, list] = {}
        for x in exchanges:
            by_step.setdefault(x.step_result_id, []).append(x)
        by_scenario: dict[int, list] = {}
        for st in steps:
            st.exchanges = by_step.get(st.id, [])
            by_scenario.setdefault(st.scenario_result_id, []).append(st)
        for s in run.scenarios:
            s.steps = by_scenario.get(s.id, [])
        return run

    def get_latest_run_id(self, bank_bic: str | None = None) -> str | None:
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM runs WHERE %(bic)s::text IS NULL OR bank_bic = %(bic)s ORDER BY created_utc DESC LIMIT 1", {"bic": bank_bic})
            row = cur.fetchone()
        return row[0] if row else None

    def get_previous_run(self, run: RunRow) -> RunRow | None:
        """The bank's previous run that has results, with its scenario outcomes."""
        with self.connect() as conn:
            with self._cursor(conn, RunRow) as cur:
                cur.execute(
                    f"""SELECT {', '.join(_columns(RunRow))} FROM runs r
                        WHERE bank_bic = %s AND id <> %s AND created_utc < %s
                          AND EXISTS (SELECT 1 FROM scenario_results s WHERE s.run_id = r.id)
                        ORDER BY created_utc DESC LIMIT 1""", (run.bank_bic, run.id, run.created_utc))
                previous = cur.fetchone()
            if previous is not None:
                with self._cursor(conn, ScenarioRow) as cur:
                    cur.execute(f"SELECT {', '.join(_columns(ScenarioRow))} FROM scenario_results WHERE run_id = %s ORDER BY ordinal", (previous.id,))
                    previous.scenarios = cur.fetchall()
        return previous

    def add_run_summary(self, summary: RunSummaryRow) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                cols = _columns(RunSummaryRow)
                values = [self._jsonb(summary.summary_json) if c == "summary_json" else getattr(summary, c) for c in cols]
                cur.execute(f"INSERT INTO run_summaries ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", values)
            conn.commit()

    def get_latest_run_summary(self, run_id: str) -> RunSummaryRow | None:
        with self.connect() as conn, self._cursor(conn, RunSummaryRow) as cur:
            cur.execute(f"SELECT {', '.join(_columns(RunSummaryRow))} FROM run_summaries WHERE run_id = %s ORDER BY created_utc DESC LIMIT 1", (run_id,))
            return cur.fetchone()

    # ---- standards and clauses -----------------------------------------------------------------------------------

    def replace_standard(self, document: StandardDocumentRow, clauses: list[ClauseRow]) -> list[int]:
        """Replaces a document's clauses (rules reference clauses by key, so they survive a re-ingest). Returns ids."""
        ids = []
        document.ingested_utc = document.ingested_utc or utc_now()
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM clauses WHERE standard = %s AND version = %s", (document.id, document.version))
                cur.execute("DELETE FROM standard_documents WHERE id = %s AND version = %s", (document.id, document.version))
                cols = _columns(StandardDocumentRow)
                cur.execute(f"INSERT INTO standard_documents ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                            [getattr(document, c) for c in cols])
                cols = [c for c in _columns(ClauseRow, exclude=("embedding",)) if c != "id"]
                for c in clauses:
                    cur.execute(f"INSERT INTO clauses ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
                                [getattr(c, name) for name in cols])
                    c.id = cur.fetchone()[0]
                    ids.append(c.id)
            conn.commit()
        return ids

    def set_clause_embeddings(self, embeddings: list[tuple[int, list[float]]]) -> None:
        with self.connect() as conn:
            with conn.cursor() as cur:
                for clause_id, vector in embeddings:
                    cur.execute("UPDATE clauses SET embedding = %s::vector WHERE id = %s", (_vector_literal(vector), clause_id))
            conn.commit()

    def list_standards(self) -> list[StandardDocumentRow]:
        with self.connect() as conn, self._cursor(conn, StandardDocumentRow) as cur:
            cur.execute(f"SELECT {', '.join(_columns(StandardDocumentRow))} FROM standard_documents ORDER BY id, version")
            return cur.fetchall()

    def get_outline(self, standard: str, version: str) -> list[ClauseRow]:
        """Clauses of a document in reading order, without text (for outlines)."""
        with self.connect() as conn, self._cursor(conn, ClauseRow) as cur:
            cols = ", ".join("'' AS text" if c == "text" else c for c in _columns(ClauseRow, exclude=("embedding",)))
            cur.execute(f"SELECT {cols} FROM clauses WHERE standard = %s AND version = %s ORDER BY ord", (standard, version))
            return cur.fetchall()

    def get_clause(self, key: str) -> ClauseRow | None:
        with self.connect() as conn, self._cursor(conn, ClauseRow) as cur:
            cur.execute(f"SELECT {_CLAUSE_COLUMNS} FROM clauses WHERE key = %s", (key,))
            return cur.fetchone()

    def get_ancestors(self, clause: ClauseRow) -> list[ClauseRow]:
        """Enclosing sections, outermost first (e.g. 6 › 6.3 › 6.3.1)."""
        numbers, n = [], clause.parent
        while n:
            numbers.append(n)
            n = n.rsplit(".", 1)[0] if "." in n else None
        if not numbers:
            return []
        with self.connect() as conn, self._cursor(conn, ClauseRow) as cur:
            cur.execute(f"SELECT {_CLAUSE_COLUMNS} FROM clauses WHERE standard = %s AND version = %s AND number = ANY(%s) ORDER BY level",
                        (clause.standard, clause.version, numbers))
            return cur.fetchall()

    def search_clauses(self, query: str, embedding=None, standard: str | None = None, limit: int = 10) -> list[ClauseHit]:
        """Hybrid search: full-text (English stemming for English documents, plain words otherwise) and, when a query
        embedding is given, meaning (cosine distance on bge-m3 vectors). Merged by reciprocal rank."""
        sql = """
            WITH q AS (SELECT websearch_to_tsquery('english', %(q)s) || websearch_to_tsquery('simple', %(q)s) AS tsq),
            fts AS (
                SELECT c.id, row_number() OVER (ORDER BY ts_rank_cd(c.search, q.tsq) DESC) AS r
                FROM clauses c, q
                WHERE c.search @@ q.tsq AND (%(std)s::text IS NULL OR c.standard = %(std)s)
                LIMIT 50),
            vec AS (
                SELECT c.id, row_number() OVER (ORDER BY c.embedding <=> %(v)s::vector) AS r
                FROM clauses c
                WHERE %(v)s::text IS NOT NULL AND c.embedding IS NOT NULL AND (%(std)s::text IS NULL OR c.standard = %(std)s)
                ORDER BY c.embedding <=> %(v)s::vector
                LIMIT 50)
            SELECT c.id, COALESCE(1.0 / (60 + fts.r), 0) + COALESCE(1.0 / (60 + vec.r), 0) AS score, fts.id IS NOT NULL, vec.id IS NOT NULL
            FROM clauses c
            LEFT JOIN fts ON fts.id = c.id
            LEFT JOIN vec ON vec.id = c.id
            WHERE fts.id IS NOT NULL OR vec.id IS NOT NULL
            ORDER BY score DESC
            LIMIT %(n)s"""
        params = {"q": any_word(query), "std": standard, "v": _vector_literal(embedding) if embedding is not None else None, "n": limit}
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                ranked = cur.fetchall()
            if not ranked:
                return []
            with self._cursor(conn, ClauseRow) as cur:
                cur.execute(f"SELECT {_CLAUSE_COLUMNS} FROM clauses WHERE id = ANY(%s)", ([r[0] for r in ranked],))
                by_id = {c.id: c for c in cur.fetchall()}
        return [ClauseHit(by_id[i], float(score), words, meaning) for i, score, words, meaning in ranked]

    # ---- rules -------------------------------------------------------------------------------------------------

    def get_rule(self, rule_id: str) -> RuleRow | None:
        with self.connect() as conn, self._cursor(conn, RuleRow) as cur:
            cur.execute(f"SELECT {_RULE_COLUMNS} FROM rules WHERE id = %s", (rule_id,))
            return cur.fetchone()

    def list_rules(self, status: str | None = None, standard: str | None = None, clause_key: str | None = None) -> list[RuleRow]:
        with self.connect() as conn, self._cursor(conn, RuleRow) as cur:
            cur.execute(f"""SELECT {_RULE_COLUMNS} FROM rules
                            WHERE (%(s)s::text IS NULL OR status = %(s)s) AND (%(d)s::text IS NULL OR standard = %(d)s)
                              AND (%(c)s::text IS NULL OR clause_key = %(c)s)
                            ORDER BY standard, clause_key, id""", {"s": status, "d": standard, "c": clause_key})
            return cur.fetchall()

    def get_rule_history(self, rule_id: str) -> list[RuleReviewRow]:
        with self.connect() as conn, self._cursor(conn, RuleReviewRow) as cur:
            cur.execute(f"SELECT {', '.join(_columns(RuleReviewRow))} FROM rule_reviews WHERE rule_id = %s ORDER BY time_utc, id", (rule_id,))
            return cur.fetchall()

    def max_rule_number(self, prefix: str) -> int:
        """Highest number used so far for ids like BG-CON-007 (prefix "BG-CON")."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM rules WHERE id LIKE %s", (prefix + "-%",))
            numbers = [int(i[len(prefix) + 1:]) for (i,) in cur.fetchall() if i[len(prefix) + 1:].isdigit()]
        return max(numbers, default=0)

    def find_similar_rules(self, vector, max_distance: float) -> list[tuple[RuleRow, float]]:
        """Draft/approved rules whose embedding is within max_distance (cosine) of the vector."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""SELECT id, embedding <=> %(v)s::vector AS d FROM rules
                               WHERE embedding IS NOT NULL AND status IN ('draft', 'approved') AND embedding <=> %(v)s::vector <= %(max)s
                               ORDER BY d LIMIT 5""", {"v": _vector_literal(vector), "max": max_distance})
                found = cur.fetchall()
            if not found:
                return []
            with self._cursor(conn, RuleRow) as cur:
                cur.execute(f"SELECT {_RULE_COLUMNS} FROM rules WHERE id = ANY(%s)", ([f[0] for f in found],))
                by_id = {r.id: r for r in cur.fetchall()}
        return [(by_id[i], float(d)) for i, d in found]

    def add_rules(self, rules: list[RuleRow], history: list[RuleReviewRow]) -> None:
        """Adds new rules and their "drafted" history rows in one transaction."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                for r in rules:
                    self._insert_rule(cur, r)
                for h in history:
                    self._insert_review(cur, h)
            conn.commit()

    def update_rule(self, rule: RuleRow, expected_updated_utc: datetime, history: RuleReviewRow, clear_embedding: bool = False) -> None:
        """Saves a changed rule and its history row. Fails if someone else changed the rule since it was read.
        The embedding is kept unless clear_embedding (the requirement text changed)."""
        cols = [c for c in _columns(RuleRow, exclude=("embedding",)) if c != "id"]
        values = [self._jsonb(getattr(rule, c)) if c == "check_json" else getattr(rule, c) for c in cols]
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT updated_utc, reviewed_by, drafted_by FROM rules WHERE id = %s FOR UPDATE", (rule.id,))
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"Rule {rule.id} no longer exists")
                if row[0] != expected_updated_utc:
                    raise ValueError(f"Rule {rule.id} was changed by someone else ({row[1] or row[2]}); reload it and try again.")
                assignments = ", ".join(f"{c} = %s" for c in cols) + (", embedding = NULL" if clear_embedding else "")
                cur.execute(f"UPDATE rules SET {assignments} WHERE id = %s", values + [rule.id])
                self._insert_review(cur, history)
            conn.commit()

    def _insert_rule(self, cur, r: RuleRow):
        cols = _columns(RuleRow)
        values = []
        for c in cols:
            v = getattr(r, c)
            if c == "check_json":
                v = self._jsonb(v)
            elif c == "embedding" and v is not None:
                v = _vector_literal(v)
            values.append(v)
        placeholders = ["%s::vector" if c == "embedding" else "%s" for c in cols]
        cur.execute(f"INSERT INTO rules ({', '.join(cols)}) VALUES ({', '.join(placeholders)})", values)

    def _insert_review(self, cur, h: RuleReviewRow):
        cur.execute("INSERT INTO rule_reviews (rule_id, time_utc, reviewer, action, note, before, after) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (h.rule_id, h.time_utc, h.reviewer, h.action, h.note, self._jsonb(h.before), self._jsonb(h.after)))

    # --- scenario drafts (Phase 3) and the rule <-> scenario link

    def add_scenario_draft(self, draft: ScenarioDraftRow) -> None:
        cols = _columns(ScenarioDraftRow)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO scenario_drafts ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                            [self._jsonb(draft.dry_run) if c == "dry_run" else getattr(draft, c) for c in cols])
            conn.commit()

    def update_scenario_draft(self, draft: ScenarioDraftRow, expected_updated_utc: datetime) -> None:
        """Saves a changed draft; fails if someone else changed it since it was read."""
        cols = [c for c in _columns(ScenarioDraftRow) if c != "id"]
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT updated_utc, reviewed_by FROM scenario_drafts WHERE id = %s FOR UPDATE", (draft.id,))
                row = cur.fetchone()
                if row is None:
                    raise ValueError(f"Scenario draft {draft.id} no longer exists")
                if row[0] != expected_updated_utc:
                    raise ValueError(f"The draft was changed by someone else ({row[1] or 'another session'}); reload it and try again.")
                cur.execute(f"UPDATE scenario_drafts SET {', '.join(c + ' = %s' for c in cols)} WHERE id = %s",
                            [self._jsonb(draft.dry_run) if c == "dry_run" else getattr(draft, c) for c in cols] + [draft.id])
            conn.commit()

    def get_scenario_draft(self, draft_id) -> ScenarioDraftRow | None:
        with self.connect() as conn, self._cursor(conn, ScenarioDraftRow) as cur:
            cur.execute(f"SELECT {', '.join(_columns(ScenarioDraftRow))} FROM scenario_drafts WHERE id::text = %s", (str(draft_id),))
            return cur.fetchone()

    def list_scenario_drafts(self, status: str | None = None, rule_id: str | None = None) -> list[ScenarioDraftRow]:
        with self.connect() as conn, self._cursor(conn, ScenarioDraftRow) as cur:
            cur.execute(f"""SELECT {', '.join(_columns(ScenarioDraftRow))} FROM scenario_drafts
                            WHERE (%(s)s::text IS NULL OR status = %(s)s) AND (%(r)s::text IS NULL OR rule_id = %(r)s)
                            ORDER BY created_utc DESC""", {"s": status, "r": rule_id})
            return cur.fetchall()

    def drafted_scenario_ids(self) -> set[str]:
        """Scenario ids already used by drafts that are not rejected (so new drafts get new ids)."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT scenario_id FROM scenario_drafts WHERE status <> 'rejected'")
            return {r[0].lower() for r in cur.fetchall()}

    def link_rule_scenario(self, rule_id: str, scenario_id: str, reviewer: str) -> None:
        """Adds the scenario to the rule's `scenarios` and records it in rule_reviews (status unchanged)."""
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("""UPDATE rules SET scenarios = array_append(scenarios, %(s)s), updated_utc = now()
                               WHERE id = %(r)s AND NOT (%(s)s = ANY(scenarios))""", {"s": scenario_id, "r": rule_id})
                if cur.rowcount:
                    cur.execute("INSERT INTO rule_reviews (rule_id, time_utc, reviewer, action, note) VALUES (%s, now(), %s, 'linked', %s)",
                                (rule_id, reviewer, f"Verified by scenario {scenario_id}"))
            conn.commit()


    def latest_outcomes(self, scenario_ids: list[str]) -> dict[tuple[str, str], tuple[str, str, datetime]]:
        """(scenario id, bank BIC) -> (outcome, run id, time) of the newest run that included the scenario."""
        if not scenario_ids:
            return {}
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT DISTINCT ON (s.scenario_id, r.bank_bic) s.scenario_id, r.bank_bic, s.outcome, r.id,
                                  coalesce(r.started_utc, r.created_utc)
                           FROM scenario_results s JOIN runs r ON r.id = s.run_id
                           WHERE s.scenario_id = ANY(%s)
                           ORDER BY s.scenario_id, r.bank_bic, coalesce(r.started_utc, r.created_utc) DESC""", (scenario_ids,))
            return {(row[0], row[1]): (row[2], row[3], row[4]) for row in cur.fetchall()}

    # --- ask-agent (Phase 4)

    def agent_query(self, sql: str, max_rows: int = 200, timeout_ms: int = 5000) -> tuple[list[str], list[tuple], bool]:
        """Runs the ask-agent's SQL logged in as aqt_agent (SELECT on the `agent` views only), one statement (prepared),
        read-only, with a statement timeout. Returns (columns, rows, truncated); database errors are raised."""
        from .connection import agent_connection, connect
        with connect(agent_connection(self.connection), register_vector=False) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {int(timeout_ms)}")
                cur.execute("SET search_path = agent")
                cur.execute(sql, prepare=True)
                if cur.description is None:
                    return [], [], False
                columns = [d.name for d in cur.description]
                rows = cur.fetchmany(max_rows + 1)
            conn.rollback()
            return columns, rows[:max_rows], len(rows) > max_rows

    def list_runs(self, bank_bic: str | None = None, since=None, until=None, limit: int = 20) -> list[RunRow]:
        with self.connect() as conn, self._cursor(conn, RunRow) as cur:
            cur.execute(f"""SELECT {', '.join(_columns(RunRow))} FROM runs
                            WHERE (%(b)s::text IS NULL OR upper(bank_bic) = upper(%(b)s))
                              AND (%(s)s::timestamptz IS NULL OR coalesce(started_utc, created_utc) >= %(s)s)
                              AND (%(u)s::timestamptz IS NULL OR coalesce(started_utc, created_utc) < %(u)s)
                            ORDER BY coalesce(started_utc, created_utc) DESC LIMIT %(l)s""",
                        {"b": bank_bic, "s": since, "u": until, "l": limit})
            return cur.fetchall()

    def scenario_history(self, scenario_id: str, bank_bic: str | None = None, limit: int = 20) -> list[tuple]:
        """(run id, bank, run start, outcome, reason) for one scenario, newest first."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT r.id, r.bank_bic, coalesce(r.started_utc, r.created_utc), s.outcome, s.reason
                           FROM scenario_results s JOIN runs r ON r.id = s.run_id
                           WHERE upper(s.scenario_id) = upper(%(s)s) AND (%(b)s::text IS NULL OR upper(r.bank_bic) = upper(%(b)s))
                           ORDER BY coalesce(r.started_utc, r.created_utc) DESC LIMIT %(l)s""",
                        {"s": scenario_id, "b": bank_bic, "l": limit})
            return cur.fetchall()

    def add_agent_question(self, q: AgentQuestionRow) -> None:
        cols = _columns(AgentQuestionRow)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"INSERT INTO agent_questions ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                            [self._jsonb(q.tool_calls) if c == "tool_calls" else getattr(q, c) for c in cols])
            conn.commit()

    def list_agent_questions(self, limit: int = 30) -> list[AgentQuestionRow]:
        with self.connect() as conn, self._cursor(conn, AgentQuestionRow) as cur:
            cur.execute(f"SELECT {', '.join(_columns(AgentQuestionRow))} FROM agent_questions ORDER BY asked_utc DESC LIMIT %s", (limit,))
            return cur.fetchall()

    def get_agent_question(self, question_id) -> AgentQuestionRow | None:
        with self.connect() as conn, self._cursor(conn, AgentQuestionRow) as cur:
            cur.execute(f"SELECT {', '.join(_columns(AgentQuestionRow))} FROM agent_questions WHERE id::text = %s", (str(question_id),))
            return cur.fetchone()

    # --- monitoring (Phase 6)

    def add_alerts(self, alerts: list[AlertRow]) -> list[AlertRow]:
        """Inserts alerts that are new (one per run, kind and scenario); returns the ones actually added."""
        cols = _columns(AlertRow)
        added = []
        with self.connect() as conn:
            with conn.cursor() as cur:
                for a in alerts:
                    cur.execute(f"INSERT INTO alerts ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
                                "ON CONFLICT (run_id, kind, scenario_id) DO NOTHING", [getattr(a, c) for c in cols])
                    if cur.rowcount:
                        added.append(a)
            conn.commit()
        return added

    def update_alert_notification(self, alert_id, notified_utc, error: str | None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE alerts SET notified_utc = %s, notify_error = %s WHERE id = %s", (notified_utc, error, alert_id))
            conn.commit()

    def list_alerts(self, open_only: bool = False, limit: int = 200) -> list[AlertRow]:
        with self.connect() as conn, self._cursor(conn, AlertRow) as cur:
            cur.execute(f"""SELECT {', '.join(_columns(AlertRow))} FROM alerts
                            WHERE NOT %(o)s OR acknowledged_utc IS NULL ORDER BY created_utc DESC LIMIT %(l)s""",
                        {"o": open_only, "l": limit})
            return cur.fetchall()

    def acknowledge_alerts(self, alert_ids: list, who: str) -> int:
        with self.connect() as conn:
            cur = conn.execute("""UPDATE alerts SET acknowledged_by = %s, acknowledged_utc = now()
                                  WHERE id::text = ANY(%s) AND acknowledged_utc IS NULL""", (who, [str(i) for i in alert_ids]))
            conn.commit()
            return cur.rowcount

    def get_monitor_state(self) -> dict[str, MonitorStateRow]:
        with self.connect() as conn, self._cursor(conn, MonitorStateRow) as cur:
            cur.execute(f"SELECT {', '.join(_columns(MonitorStateRow))} FROM monitor_state")
            return {r.name: r for r in cur.fetchall()}

    def set_monitor_state(self, state: MonitorStateRow) -> None:
        cols = _columns(MonitorStateRow)
        with self.connect() as conn:
            conn.execute(f"""INSERT INTO monitor_state ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})
                             ON CONFLICT (name) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in cols if c != 'name')}""",
                         [getattr(state, c) for c in cols])
            conn.commit()

    def prune_exchanges(self, older_than_days: int) -> int:
        """Retention: deletes stored HTTP exchanges older than the given number of days (results, summaries and alerts
        are kept). Returns the number deleted."""
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM exchanges WHERE started_utc < now() - make_interval(days => %s)", (int(older_than_days),))
            conn.commit()
            return cur.rowcount

    def pass_rate_series(self, days: int = 30) -> dict[str, list[tuple]]:
        """bank -> [(run id, started, pass rate, failed + errors)] of completed runs, oldest first."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT bank_bic, id, coalesce(started_utc, created_utc), pass_rate, failed + errors FROM runs
                           WHERE status = 'Completed' AND passed + failed + errors > 0
                             AND coalesce(started_utc, created_utc) >= now() - make_interval(days => %s)
                           ORDER BY coalesce(started_utc, created_utc)""", (int(days),))
            series: dict[str, list[tuple]] = {}
            for bank, *rest in cur.fetchall():
                series.setdefault(bank, []).append(tuple(rest))
            return series

    def outcome_sequences(self, days: int = 30) -> dict[tuple[str, str], list[tuple]]:
        """(bank, scenario) -> [(run id, outcome)] oldest first, Passed/Failed/Error only (for flakiness)."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT r.bank_bic, s.scenario_id, r.id, s.outcome FROM scenario_results s JOIN runs r ON r.id = s.run_id
                           WHERE s.outcome IN ('Passed', 'Failed', 'Error')
                             AND coalesce(r.started_utc, r.created_utc) >= now() - make_interval(days => %s)
                           ORDER BY coalesce(r.started_utc, r.created_utc)""", (int(days),))
            result: dict[tuple[str, str], list[tuple]] = {}
            for bank, scenario, run_id, outcome in cur.fetchall():
                result.setdefault((bank, scenario), []).append((run_id, outcome))
            return result

    def slowest_endpoints(self, days: int = 30, limit: int = 10) -> list[tuple]:
        """(bank, method, path with ids replaced, calls, avg ms, max ms), slowest average first."""
        with self.connect() as conn, conn.cursor() as cur:
            cur.execute(r"""SELECT r.bank_bic, x.method,
                                   regexp_replace(regexp_replace(split_part(x.url, '?', 1), '^https?://[^/]+', ''),
                                                  '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}', '{id}', 'g') AS path,
                                   count(*), round(avg(x.duration_ms)), max(x.duration_ms)
                            FROM exchanges x JOIN runs r ON r.id = x.run_id
                            WHERE x.status_code IS NOT NULL AND x.started_utc >= now() - make_interval(days => %s)
                            GROUP BY 1, 2, 3 HAVING count(*) >= 2 ORDER BY 5 DESC LIMIT %s""", (int(days), int(limit)))
            return cur.fetchall()

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id():
    return uuid4()
