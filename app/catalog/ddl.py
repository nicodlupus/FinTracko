"""Turns a catalog declaration into a real user table.

`declare_table` is the one entry point: give it a display name, a kind, and
a list of field specs, and it writes the catalog rows (_ft_table, _ft_field,
_ft_choice, _ft_computed) *and* creates the physical `u_*` table, all in one
transaction. Nothing here accepts raw SQL from the caller -- every identifier
goes through app.catalog.validate first.

Column shape per field type:
    date, text     -> one TEXT column
    number         -> one REAL column
    choice         -> one TEXT column, storing the chosen label. Not
                       CHECK-constrained: choices can be added after the
                       table exists, and SQLite can't alter a CHECK. Entries
                       (Phase 2) validate the label against _ft_choice.
    money          -> THREE columns: <name>, <name>_currency, <name>_rate_to_base.
                       The rate is captured at entry time so historical rows
                       never move when exchange rates change later.
    computed       -> one REAL GENERATED ALWAYS AS (<expr>) VIRTUAL column.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from app.catalog.schema import TABLE_KINDS, FIELD_TYPES, FIELD_ROLES, utcnow
from app.catalog.validate import (
    sql_name_for_table,
    sql_name_for_field,
    validate_sql_identifier,
)


def _column_ddl_for_field(sql_name: str, type_: str, *, required: bool, expr: str | None) -> list[str]:
    """One field spec -> one or more physical column definitions."""
    not_null = " NOT NULL" if required else ""

    if type_ in ("date", "text", "choice"):
        return [f"{sql_name} TEXT{not_null}"]
    if type_ == "number":
        return [f"{sql_name} REAL{not_null}"]
    if type_ == "money":
        return [
            f"{sql_name} REAL{not_null}",
            f"{sql_name}_currency TEXT{not_null}",
            f"{sql_name}_rate_to_base REAL{not_null}",
        ]
    if type_ == "computed":
        if not expr:
            raise ValueError(f"computed field {sql_name!r} needs an expr")
        # VIRTUAL, not STORED: recomputed on read, cheap for personal-finance
        # volumes, and avoids SQLite's stricter rules around altering STORED
        # generated columns later.
        return [f"{sql_name} REAL GENERATED ALWAYS AS ({expr}) VIRTUAL"]
    raise ValueError(f"unknown field type {type_!r}")


def build_create_table_sql(table_sql_name: str, field_specs: list[dict[str, Any]]) -> str:
    """Compose the full CREATE TABLE statement for a user table.

    field_specs: dicts with keys sql_name, type, required, expr (expr only
    used for type == 'computed').
    """
    validate_sql_identifier(table_sql_name.removeprefix("u_"))

    columns = [
        "id INTEGER PRIMARY KEY",
        "created_at TEXT NOT NULL",
        "updated_at TEXT NOT NULL",
        "deleted_at TEXT",
    ]
    for spec in field_specs:
        columns.extend(
            _column_ddl_for_field(
                spec["sql_name"],
                spec["type"],
                required=spec.get("required", False),
                expr=spec.get("expr"),
            )
        )

    body = ",\n    ".join(columns)
    return f"CREATE TABLE IF NOT EXISTS {table_sql_name} (\n    {body}\n)"


def _funding_edges(conn: sqlite3.Connection) -> dict[int, set[int]]:
    """table_id -> set of table_ids it funds, from live funding choices."""
    rows = conn.execute(
        """
        SELECT f.table_id AS source, c.funds_table_id AS target
        FROM _ft_choice c
        JOIN _ft_field f ON f.id = c.field_id
        WHERE c.deleted_at IS NULL AND c.funds_table_id IS NOT NULL
        """
    ).fetchall()
    edges: dict[int, set[int]] = {}
    for row in rows:
        edges.setdefault(row["source"], set()).add(row["target"])
    return edges


def would_create_cycle(conn: sqlite3.Connection, source_table_id: int, funds_table_id: int) -> bool:
    """Would adding the edge source_table_id -> funds_table_id create a cycle?

    A choice funding its own table (source == funds) is the degenerate,
    length-one case of this and is caught by the same check -- no special
    case needed.
    """
    if source_table_id == funds_table_id:
        return True

    edges = _funding_edges(conn)
    stack = [funds_table_id]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if node == source_table_id:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(edges.get(node, ()))
    return False


def declare_table(
    conn: sqlite3.Connection,
    name: str,
    kind: str,
    fields: list[dict[str, Any]],
) -> int:
    """Declare a user table: catalog rows + the physical CREATE TABLE.

    fields: list of dicts, each with:
        name      (str, required)   -- display name, e.g. "Amount Paid"
        type      (str, required)   -- one of FIELD_TYPES
        role      (str, optional)   -- one of FIELD_ROLES, default 'none'
        required  (bool, optional)  -- NOT NULL, default False
        expr      (str, optional)   -- SQL expression, only for type='computed'
        choices   (list, optional)  -- only for type='choice':
                      [{"label": str, "funds_table_id": int | None}, ...]

    Runs as one transaction: either the whole table (catalog + physical)
    is created, or none of it is.

    Returns the new table's _ft_table.id.
    """
    if kind not in TABLE_KINDS:
        raise ValueError(f"kind must be one of {TABLE_KINDS}, got {kind!r}")

    table_sql_name = sql_name_for_table(name)
    now = utcnow()

    with conn:
        cur = conn.execute(
            "INSERT INTO _ft_table (name, sql_name, kind, created_at) VALUES (?, ?, ?, ?)",
            (name, table_sql_name, kind, now),
        )
        table_id = cur.lastrowid

        field_specs: list[dict[str, Any]] = []
        for position, field in enumerate(fields):
            if field["type"] not in FIELD_TYPES:
                raise ValueError(f"field {field['name']!r}: unknown type {field['type']!r}")
            role = field.get("role", "none")
            if role not in FIELD_ROLES:
                raise ValueError(f"field {field['name']!r}: unknown role {role!r}")

            field_sql_name = sql_name_for_field(field["name"])
            field_cur = conn.execute(
                "INSERT INTO _ft_field (table_id, name, sql_name, type, role, required, position) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (table_id, field["name"], field_sql_name, field["type"], role,
                 int(field.get("required", False)), position),
            )
            field_id = field_cur.lastrowid

            if field["type"] == "computed":
                expr = field.get("expr")
                if not expr:
                    raise ValueError(f"field {field['name']!r}: computed fields need an expr")
                conn.execute(
                    "INSERT INTO _ft_computed (field_id, expr) VALUES (?, ?)",
                    (field_id, expr),
                )

            if field["type"] == "choice":
                for choice_position, choice in enumerate(field.get("choices", [])):
                    funds_table_id = choice.get("funds_table_id")
                    if funds_table_id is not None and would_create_cycle(conn, table_id, funds_table_id):
                        raise ValueError(
                            f"choice {choice['label']!r} on field {field['name']!r} "
                            f"would create a funding cycle"
                        )
                    conn.execute(
                        "INSERT INTO _ft_choice (field_id, label, position, funds_table_id) "
                        "VALUES (?, ?, ?, ?)",
                        (field_id, choice["label"], choice_position, funds_table_id),
                    )

            field_specs.append({
                "sql_name": field_sql_name,
                "type": field["type"],
                "required": field.get("required", False),
                "expr": field.get("expr"),
            })

        create_sql = build_create_table_sql(table_sql_name, field_specs)
        conn.execute(create_sql)

    return table_id
