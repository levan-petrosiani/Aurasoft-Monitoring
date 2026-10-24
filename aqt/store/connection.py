"""
Where the store's PostgreSQL connection comes from, and the schema migrations.

The connection string can be a libpq string (``host=... dbname=...``), a URL (``postgresql://...``) or the C# tool's
``Host=...;Port=...;Database=...;Username=...;Password=...`` form, so existing AQT_DB values keep working.
"""

import os
import re
from importlib import resources

ENVIRONMENT_VARIABLE = "AQT_DB"

# Matches docker-compose.yml (local development only).
DOCKER_COMPOSE_DEFAULT = "Host=localhost;Port=5435;Database=aqt;Username=aqt;Password=aqt_dev"

# The C# tool's last EF Core migration: a database that has it already contains migration 0001.
_EF_BASELINE = "20261024084136_StandardsAndRules"

_KEY_MAP = {"host": "host", "server": "host", "port": "port", "database": "dbname", "username": "user",
            "user id": "user", "userid": "user", "user": "user", "password": "password", "timeout": "connect_timeout",
            "sslmode": "sslmode", "ssl mode": "sslmode"}


def from_environment() -> str | None:
    value = os.environ.get(ENVIRONMENT_VARIABLE)
    return value or None


def to_libpq(connection: str) -> str:
    """Accepts the C# ``Key=Value;`` form as well as libpq strings and URLs."""
    text = connection.strip()
    if text.startswith(("postgres://", "postgresql://")) or ";" not in text and "=" in text and " " in text:
        return text
    parts = []
    for item in filter(None, (p.strip() for p in text.split(";"))):
        key, _, value = item.partition("=")
        mapped = _KEY_MAP.get(key.strip().lower())
        if mapped is None:
            raise ValueError(f"Unknown connection string key '{key}'")
        value = value.strip()
        if re.search(r"[\s'\\]", value) or value == "":
            value = "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
        parts.append(f"{mapped}={value}")
    return " ".join(parts)


def connect(connection: str, *, register_vector: bool = True):
    """Opens a connection (autocommit off). pgvector types are registered once the extension exists."""
    import psycopg  # imported here: the core tool does not need the database packages

    conn = psycopg.connect(to_libpq(connection), connect_timeout=10)
    if register_vector:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_type WHERE typname = 'vector'")
            exists = cur.fetchone() is not None
        conn.rollback()
        if exists:
            from pgvector.psycopg import register_vector as _register
            _register(conn)
    return conn


def _migration_files() -> list[tuple[str, str]]:
    folder = resources.files("aqt.store").joinpath("migrations")
    files = sorted(p.name for p in folder.iterdir() if p.name.endswith(".sql"))
    return [(name.split("_", 1)[0], folder.joinpath(name).read_text(encoding="utf-8")) for name in files]


def pending_migrations(connection: str) -> list[str]:
    with connect(connection, register_vector=False) as conn:
        applied = _applied(conn)
        return [version for version, _ in _migration_files() if version not in applied]


AGENT_ROLE = "aqt_agent"
AGENT_DEV_PASSWORD = "aqt_agent_dev"


def agent_password(env=os.environ) -> str:
    return env.get("AQT_AGENT_DB_PASSWORD") or AGENT_DEV_PASSWORD


def agent_connection(connection: str) -> str:
    """The main connection string with the ask-agent's read-only login instead of the main user."""
    from psycopg.conninfo import make_conninfo
    return make_conninfo(to_libpq(connection), user=AGENT_ROLE, password=agent_password())


def _set_agent_password(conn) -> None:
    from psycopg import sql
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (AGENT_ROLE,))
        if cur.fetchone() is None:
            conn.rollback()
            return
        cur.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(AGENT_ROLE), sql.Literal(agent_password())))
    conn.commit()


def migrate(connection: str) -> list[str]:
    """Creates or upgrades the schema; returns the versions applied now."""
    applied_now = []
    with connect(connection, register_vector=False) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS aqt_schema_migrations (version text PRIMARY KEY, applied_utc timestamptz NOT NULL DEFAULT now(), note text)")
        conn.commit()

        applied = _applied(conn)
        if not applied and _created_by_csharp_tool(conn):
            with conn.cursor() as cur:
                cur.execute("INSERT INTO aqt_schema_migrations (version, note) VALUES ('0001', 'created by the C# tool (EF Core)')")
            conn.commit()
            applied = {"0001"}

        for version, sql in _migration_files():
            if version in applied:
                continue
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute("INSERT INTO aqt_schema_migrations (version) VALUES (%s)", (version,))
            conn.commit()
            applied_now.append(version)
        try:
            _set_agent_password(conn)
        except Exception as ex:  # noqa: BLE001 - no right to change roles: the ask-agent's SQL tool will say it cannot log in
            conn.rollback()
            print(f"  store: could not set the ask-agent role's password ({ex}); its SQL tool needs AQT_AGENT_DB_PASSWORD to match")
    return applied_now


def _applied(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('aqt_schema_migrations') IS NOT NULL")
        if not cur.fetchone()[0]:
            conn.rollback()
            return set()
        cur.execute("SELECT version FROM aqt_schema_migrations")
        result = {row[0] for row in cur.fetchall()}
    conn.rollback()
    return result


def _created_by_csharp_tool(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('\"__EFMigrationsHistory\"') IS NOT NULL")
        if not cur.fetchone()[0]:
            conn.rollback()
            return False
        cur.execute('SELECT 1 FROM "__EFMigrationsHistory" WHERE migration_id = %s', (_EF_BASELINE,))
        found = cur.fetchone() is not None
    conn.rollback()
    return found
