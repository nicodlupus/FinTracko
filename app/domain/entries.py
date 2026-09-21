"""Row-level CRUD for user tables: insert, update, soft-delete, read.

Callers speak in terms of a table's declared field NAMES ("Amount", "Hours"),
never physical column names -- this module is the translation layer between
what the catalog declared and the real u_* columns underneath.

Every write is audited: `_ft_audit` gets a before/after JSON snapshot, so a
soft-deleted or overwritten value is never actually unrecoverable (Phase 0's
snapshots are the other half of that durability story, still to come).

Money values are passed as either a plain number (uses the environment's
base currency) or a dict `{"amount": ..., "currency": "USD"}`. The exchange
rate is resolved once, at write time, via app.domain.currency, and frozen
into the row -- a later change to _ft_rate never moves a historical amount.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.catalog.schema import utcnow
from app.domain import computed, currency


def _table_sql_name(conn: sqlite3.Connection, table_id: int) -> str:
    row = conn.execute(
        "SELECT sql_name FROM _ft_table WHERE id = ? AND deleted_at IS NULL", (table_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"no live table with id {table_id}")
    return row["sql_name"]


def _live_fields(conn: sqlite3.Connection, table_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT id, name, sql_name, type, role, required FROM _ft_field "
        "WHERE table_id = ? AND deleted_at IS NULL ORDER BY position",
        (table_id,),
    ).fetchall()
    return [
        {"id": r["id"], "name": r["name"], "sql_name": r["sql_name"], "type": r["type"],
         "role": r["role"], "required": bool(r["required"])}
        for r in rows
    ]


def _live_choice_labels(conn: sqlite3.Connection, field_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT label FROM _ft_choice WHERE field_id = ? AND deleted_at IS NULL", (field_id,)
    ).fetchall()
    return {r["label"] for r in rows}


def _parse_money(conn: sqlite3.Connection, value: Any) -> tuple[float, str]:
    if isinstance(value, dict):
        if "amount" not in value:
            raise ValueError("a money value given as a dict needs an 'amount' key")
        return value["amount"], value.get("currency", currency.base_currency(conn))
    if isinstance(value, (int, float)):
        return value, currency.base_currency(conn)
    raise ValueError(f"money value must be a number or {{'amount':.., 'currency':..}}, got {value!r}")


def _as_of_date(fields: list[dict], values: dict) -> str | None:
    """Use the row's own date field, if one is present in this write, to
    pick a historically-correct exchange rate. Falls back to 'latest known'
    when there is no date field or it isn't part of this write."""
    for f in fields:
        if f["role"] == "date" and f["name"] in values:
            return values[f["name"]]
    return None


def _write_audit(conn: sqlite3.Connection, sql_name: str, row_id: int, op: str,
                  before: sqlite3.Row | None, after: sqlite3.Row | None) -> None:
    conn.execute(
        "INSERT INTO _ft_audit (ts, sql_name, row_id, op, before_json, after_json) VALUES (?, ?, ?, ?, ?, ?)",
        (
            utcnow(), sql_name, row_id, op,
            json.dumps(dict(before)) if before is not None else None,
            json.dumps(dict(after)) if after is not None else None,
        ),
    )


def _fetch_row(conn: sqlite3.Connection, sql_name: str, row_id: int, *, include_deleted: bool = False) -> sqlite3.Row | None:
    clause = "" if include_deleted else "AND deleted_at IS NULL"
    return conn.execute(
        f"SELECT * FROM {sql_name} WHERE id = ? {clause}", (row_id,)
    ).fetchone()


def _columns_for_value(conn: sqlite3.Connection, field: dict, value: Any, as_of: str | None) -> tuple[list[str], list[Any]]:
    """One field + its value -> the physical column(s) and param(s) to write."""
    computed.reject_write(field)

    if field["type"] == "money":
        amount, code = _parse_money(conn, value)
        rate = currency.rate_to_base(conn, code, as_of=as_of)
        return (
            [field["sql_name"], f"{field['sql_name']}_currency", f"{field['sql_name']}_rate_to_base"],
            [amount, code, rate],
        )

    if field["type"] == "choice":
        valid = _live_choice_labels(conn, field["id"])
        if value not in valid:
            raise ValueError(f"{value!r} is not a valid choice for {field['name']!r}; choices are {sorted(valid)}")
        return [field["sql_name"]], [value]

    return [field["sql_name"]], [value]


def insert_entry(conn: sqlite3.Connection, table_id: int, values: dict[str, Any]) -> int:
    """Insert one row. `values` keys are field display names.

    Missing required fields, unknown field names, invalid choice labels, and
    any attempt to set a computed field all raise ValueError before anything
    is written.
    """
    table_sql_name = _table_sql_name(conn, table_id)
    fields = _live_fields(conn, table_id)
    field_by_name = {f["name"]: f for f in fields}

    unknown = set(values) - set(field_by_name)
    if unknown:
        raise ValueError(f"unknown field(s): {sorted(unknown)}")

    as_of = _as_of_date(fields, values)
    now = utcnow()
    columns = ["created_at", "updated_at"]
    params: list[Any] = [now, now]

    for field in fields:
        if computed.is_computed(field):
            if field["name"] in values:
                computed.reject_write(field)  # always raises -- caller tried to set a derived value
            continue
        if field["name"] not in values:
            if field["required"]:
                raise ValueError(f"{field['name']!r} is required")
            continue
        cols, vals = _columns_for_value(conn, field, values[field["name"]], as_of)
        columns.extend(cols)
        params.extend(vals)

    placeholders = ", ".join("?" for _ in columns)
    column_list = ", ".join(columns)

    with conn:
        cur = conn.execute(
            f"INSERT INTO {table_sql_name} ({column_list}) VALUES ({placeholders})", params
        )
        row_id = cur.lastrowid
        after = _fetch_row(conn, table_sql_name, row_id)
        _write_audit(conn, table_sql_name, row_id, "insert", before=None, after=after)

    return row_id


def update_entry(conn: sqlite3.Connection, table_id: int, row_id: int, values: dict[str, Any]) -> None:
    """Update the given fields on one row (partial -- fields not mentioned
    are left untouched). Raises if the row doesn't exist, is soft-deleted,
    a name is unknown, a required field is explicitly cleared to None, or a
    computed field is targeted.
    """
    table_sql_name = _table_sql_name(conn, table_id)
    fields = _live_fields(conn, table_id)
    field_by_name = {f["name"]: f for f in fields}

    unknown = set(values) - set(field_by_name)
    if unknown:
        raise ValueError(f"unknown field(s): {sorted(unknown)}")

    before = _fetch_row(conn, table_sql_name, row_id)
    if before is None:
        raise ValueError(f"no live row {row_id} in this table")

    as_of = _as_of_date(fields, values)
    columns = ["updated_at"]
    params: list[Any] = [utcnow()]

    for name, value in values.items():
        field = field_by_name[name]
        if field["required"] and value is None:
            raise ValueError(f"{name!r} is required and cannot be cleared")
        cols, vals = _columns_for_value(conn, field, value, as_of)
        columns.extend(cols)
        params.extend(vals)

    set_clause = ", ".join(f"{c} = ?" for c in columns)
    with conn:
        conn.execute(
            f"UPDATE {table_sql_name} SET {set_clause} WHERE id = ? AND deleted_at IS NULL",
            [*params, row_id],
        )
        after = _fetch_row(conn, table_sql_name, row_id)
        _write_audit(conn, table_sql_name, row_id, "update", before=before, after=after)


def soft_delete_entry(conn: sqlite3.Connection, table_id: int, row_id: int) -> None:
    """Mark a row deleted without removing it -- it stays for the audit
    trail and can be un-deleted by clearing deleted_at directly if needed."""
    table_sql_name = _table_sql_name(conn, table_id)
    before = _fetch_row(conn, table_sql_name, row_id)
    if before is None:
        raise ValueError(f"no live row {row_id} in this table")

    now = utcnow()
    with conn:
        conn.execute(
            f"UPDATE {table_sql_name} SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            (now, now, row_id),
        )
        after = _fetch_row(conn, table_sql_name, row_id, include_deleted=True)
        _write_audit(conn, table_sql_name, row_id, "delete", before=before, after=after)


def get_entry(conn: sqlite3.Connection, table_id: int, row_id: int, *, include_deleted: bool = False) -> dict | None:
    table_sql_name = _table_sql_name(conn, table_id)
    row = _fetch_row(conn, table_sql_name, row_id, include_deleted=include_deleted)
    return dict(row) if row is not None else None


def list_entries(conn: sqlite3.Connection, table_id: int, *, include_deleted: bool = False) -> list[dict]:
    table_sql_name = _table_sql_name(conn, table_id)
    clause = "" if include_deleted else "WHERE deleted_at IS NULL"
    rows = conn.execute(f"SELECT * FROM {table_sql_name} {clause} ORDER BY id").fetchall()
    return [dict(r) for r in rows]
