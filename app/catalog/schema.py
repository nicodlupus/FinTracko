"""
This is the catalog schema for the FT env file

The catalog is data which describe the schema. the user creates tables at running time.
The catalog is responsible to record what has been declared and the DDL builder reads the catalog
to emit the real CREATE TABLE statements
"""

# importing the necessary libraries and packages
from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

TABLE_KINDS = ("income", "obligation", "expense", "ledger")
ENV_KINDS = ("current", "forecast")

# ddl builder
_DDL = """
CREATE TABLE IF NOT EXISTS _ft_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS _ft_table (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    sql_name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('income', 'obligation', 'expense', 'ledger')),
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);

-- Uniqueness ignoring soft-deleted rows
CREATE UNIQUE INDEX IF NOT EXISTS ix_ft_table_name_live ON _ft_table (name) WHERE deleted_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS ix_ft_table_sqlname_live ON _ft_table (sql_name) WHERE deleted_at IS NULL;
"""

def utcnow() -> str:
     """ISO-8601 UTC timestamp. Text in this format sorts chronologically."""
     return datetime.now(timezone.utc).isoformat(timespec="seconds")
 
def open_env(
    path: str | Path,
    *,
    env_name: str | None=None,
    env_kind: str = "current",
    base_currency: str = "EUR",
) -> sqlite3.Connection:
    # Open env file and return connection, calling it twice on the same path leaves file unchanged
    path = Path(path)
    if env_kind not in ENV_KINDS:
        raise ValueError(f"env_kind MUST be one of the {ENV_KINDS}, got {env_kind!r}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    
    # per connection note stored in the file
    conn.execute("PRAGMA foreign_keys = ON")
    
    _guard_schema_version(conn)
    conn.executescript(_DDL)
    _seed_meta(conn, path, env_name, env_kind, base_currency)
    return conn

def _guard_schema_version(conn: sqlite3.Connection) -> None:
    #refuse to open a file written by a new build of the app
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_ft_meta'"
    ).fetchone()
    if exists is None:
        return  # fresh file, nothing to guard

    found = conn.execute(
        "SELECT value FROM _ft_meta WHERE key = 'schema_version'"
    ).fetchone()
    if found and int(found["value"]) > SCHEMA_VERSION:
        raise RuntimeError(
            f"file was written with catalog schema v{found['value']}, "
            f"this build understands v{SCHEMA_VERSION}"
        )


def _seed_meta(
    conn: sqlite3.Connection,
    path: Path,
    env_name: str | None,
    env_kind: str,
    base_currency: str,
) -> None:
    """Write the environment's identity -- once, on first creation."""
    values = {
        "schema_version": str(SCHEMA_VERSION),
        "env_kind": env_kind,
        "env_name": env_name or path.stem,
        "base_currency": base_currency,
        "created_at": utcnow(),
    }
    with conn:  # transaction: all five rows land, or none do
        conn.executemany(
            "INSERT OR IGNORE INTO _ft_meta (key, value) VALUES (?, ?)",
            list(values.items()),
        )


def read_meta(conn: sqlite3.Connection) -> dict[str, str]:
    """The environment's identity as a plain dict."""
    return {r["key"]: r["value"]
            for r in conn.execute("SELECT key, value FROM _ft_meta")}