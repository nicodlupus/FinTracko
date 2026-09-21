"""Changing a table after it already has rows.

Two kinds of change live here, because SQLite handles them very differently:

  add_field()         -- a brand new column. SQLite's ALTER TABLE ADD COLUMN
                          handles this directly, so it's cheap.

  change_field_type()  -- an existing field changing shape (e.g. number ->
                          money, which is one column becoming three).
                          SQLite cannot ALTER a column's type or drop a
                          GENERATED column dependency in place, so this goes
                          through rebuild_user_table(): create the table
                          fresh from the current catalog under a temp name,
                          copy over whatever data still fits, drop the old
                          table, rename the new one into its place. All in
                          one transaction -- either the rebuild completes or
                          nothing changes.

A third thing lives here too: apply_pending_migrations(), which upgrades an
*old .ftdb file's catalog shape* (not a user table) when SCHEMA_VERSION has
moved on since the file was written. It is currently a no-op in practice --
SCHEMA_VERSION is still 1 -- but the mechanism is real and tested, ready for
the day a catalog table itself needs to change shape.

Durability note: the plan calls for a snapshot before any structural change.
That waits on app/storage/snapshots.py (Phase 0), which doesn't exist yet --
these functions are written so a `snapshot(path)` call can be dropped in at
the top of each one later without changing anything else.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable

from app.catalog.schema import TABLE_KINDS, FIELD_TYPES, FIELD_ROLES, utcnow, read_meta, SCHEMA_VERSION
from app.catalog.validate import sql_name_for_field
from app.catalog.ddl import build_create_table_sql, column_ddl_for_field, would_create_cycle


def _table_sql_name(conn: sqlite3.Connection, table_id: int) -> str:
    row = conn.execute(
        "SELECT sql_name FROM _ft_table WHERE id = ? AND deleted_at IS NULL", (table_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no live table with id {table_id}")
    return row["sql_name"]


def add_field(
    conn: sqlite3.Connection,
    table_id: int,
    field: dict[str, Any],
) -> int:
    """Add a field to a table that may already have rows.

    Same field dict shape as declare_table(). One deliberate difference:
    the physical column is always created nullable, regardless of
    field['required'] -- SQLite can only add a NOT NULL column to a
    non-empty table if every existing row gets a default value for it,
    which we have no honest one for. 'required' is still recorded in the
    catalog and is enforced by the entry layer (Phase 2) for rows written
    from here on; it just isn't a physical constraint on this column.
    """
    if field["type"] not in FIELD_TYPES:
        raise ValueError(f"field {field['name']!r}: unknown type {field['type']!r}")
    role = field.get("role", "none")
    if role not in FIELD_ROLES:
        raise ValueError(f"field {field['name']!r}: unknown role {role!r}")

    with conn:
        table_sql_name = _table_sql_name(conn, table_id)
        position = conn.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM _ft_field "
            "WHERE table_id = ? AND deleted_at IS NULL",
            (table_id,),
        ).fetchone()["p"]

        field_sql_name = sql_name_for_field(field["name"])
        cur = conn.execute(
            "INSERT INTO _ft_field (table_id, name, sql_name, type, role, required, position) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (table_id, field["name"], field_sql_name, field["type"], role,
             int(field.get("required", False)), position),
        )
        field_id = cur.lastrowid

        if field["type"] == "computed":
            expr = field.get("expr")
            if not expr:
                raise ValueError(f"field {field['name']!r}: computed fields need an expr")
            conn.execute("INSERT INTO _ft_computed (field_id, expr) VALUES (?, ?)", (field_id, expr))

        if field["type"] == "choice":
            for choice_position, choice in enumerate(field.get("choices", [])):
                funds_table_id = choice.get("funds_table_id")
                if funds_table_id is not None and would_create_cycle(conn, table_id, funds_table_id):
                    raise ValueError(
                        f"choice {choice['label']!r} on field {field['name']!r} "
                        f"would create a funding cycle"
                    )
                conn.execute(
                    "INSERT INTO _ft_choice (field_id, label, position, funds_table_id) VALUES (?, ?, ?, ?)",
                    (field_id, choice["label"], choice_position, funds_table_id),
                )

        # never NOT NULL here -- see docstring
        for column_ddl in column_ddl_for_field(field_sql_name, field["type"], required=False, expr=field.get("expr")):
            conn.execute(f"ALTER TABLE {table_sql_name} ADD COLUMN {column_ddl}")

    return field_id


def rebuild_user_table(conn: sqlite3.Connection, table_id: int) -> None:
    """Recreate a user table's physical shape to match its current catalog.

    Used whenever a change can't be expressed as ADD COLUMN. Any column that
    exists under the same name in both the old and new shape is copied;
    anything only in the new shape starts NULL for existing rows; generated
    (computed) columns are never copied into directly -- SQLite forbids it,
    and they recompute themselves once their source columns are in place.
    """
    with conn:
        table_sql_name = _table_sql_name(conn, table_id)

        fields = conn.execute(
            "SELECT sql_name, type FROM _ft_field WHERE table_id = ? AND deleted_at IS NULL ORDER BY position",
            (table_id,),
        ).fetchall()
        field_specs = [{"sql_name": f["sql_name"], "type": f["type"], "required": False,
                         "expr": _computed_expr(conn, table_id, f["sql_name"])} for f in fields]

        new_sql_name = f"{table_sql_name}__rebuild"
        conn.execute(f"DROP TABLE IF EXISTS {new_sql_name}")
        conn.execute(build_create_table_sql(new_sql_name, field_specs))

        old_columns = {
            row["name"] for row in conn.execute(f"PRAGMA table_xinfo({table_sql_name})")
            if row["hidden"] not in (2, 3)  # exclude STORED/VIRTUAL generated columns
        }
        new_columns = {"id", "created_at", "updated_at", "deleted_at"}
        for spec in field_specs:
            if spec["type"] != "computed":
                if spec["type"] == "money":
                    new_columns |= {spec["sql_name"], f"{spec['sql_name']}_currency", f"{spec['sql_name']}_rate_to_base"}
                else:
                    new_columns.add(spec["sql_name"])

        copy_columns = sorted(old_columns & new_columns)
        column_list = ", ".join(copy_columns)
        conn.execute(
            f"INSERT INTO {new_sql_name} ({column_list}) SELECT {column_list} FROM {table_sql_name}"
        )

        conn.execute(f"DROP TABLE {table_sql_name}")
        conn.execute(f"ALTER TABLE {new_sql_name} RENAME TO {table_sql_name}")


def _computed_expr(conn: sqlite3.Connection, table_id: int, field_sql_name: str) -> str | None:
    row = conn.execute(
        "SELECT c.expr FROM _ft_computed c JOIN _ft_field f ON f.id = c.field_id "
        "WHERE f.table_id = ? AND f.sql_name = ?",
        (table_id, field_sql_name),
    ).fetchone()
    return row["expr"] if row else None


def change_field_type(conn: sqlite3.Connection, field_id: int, new_type: str, *, expr: str | None = None) -> None:
    """Change an existing field's type and rebuild its table to match.

    This is the case SQLite genuinely cannot do in place: a column's type
    can't be altered, and a money field is a type change from one column to
    three. new_type == 'computed' requires expr.
    """
    if new_type not in FIELD_TYPES:
        raise ValueError(f"unknown type {new_type!r}")

    with conn:
        row = conn.execute("SELECT table_id FROM _ft_field WHERE id = ?", (field_id,)).fetchone()
        if row is None:
            raise ValueError(f"no field with id {field_id}")
        table_id = row["table_id"]

        conn.execute("UPDATE _ft_field SET type = ? WHERE id = ?", (new_type, field_id))
        conn.execute("DELETE FROM _ft_computed WHERE field_id = ?", (field_id,))
        if new_type == "computed":
            if not expr:
                raise ValueError("changing a field to 'computed' requires expr")
            conn.execute("INSERT INTO _ft_computed (field_id, expr) VALUES (?, ?)", (field_id, expr))

        rebuild_user_table(conn, table_id)


# --- catalog (not user-table) schema migrations -----------------------------
#
# Keyed by the version each function upgrades TO: MIGRATIONS[2] takes a file
# from v1 to v2. Empty today because SCHEMA_VERSION is still 1; exists so
# the day a _ft_* table's shape needs to change, there's already a tested
# path for it that doesn't touch this file's callers.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {}


def apply_pending_migrations(conn: sqlite3.Connection) -> list[int]:
    """Run any catalog migrations newer than this file's current version.

    Returns the list of versions applied, in order. Each migration function
    runs in the same transaction as the _ft_meta/_ft_migration bookkeeping
    for it -- a failing migration leaves the file at its old version.
    """
    current = int(read_meta(conn)["schema_version"])
    applied = []
    for version in sorted(v for v in MIGRATIONS if v > current):
        with conn:
            MIGRATIONS[version](conn)
            conn.execute("UPDATE _ft_meta SET value = ? WHERE key = 'schema_version'", (str(version),))
            conn.execute(
                "INSERT INTO _ft_migration (version, applied_at) VALUES (?, ?)", (version, utcnow())
            )
        applied.append(version)
    return applied
