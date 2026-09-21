"""Tests for app.catalog.migrate: add_field, rebuild, catalog migrations."""

import sqlite3

import pytest

from app.catalog.schema import open_env, read_meta
from app.catalog.ddl import declare_table
from app.catalog.migrate import (
    add_field,
    change_field_type,
    apply_pending_migrations,
    MIGRATIONS,
)


def test_add_field_to_table_with_existing_rows(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    table_id = declare_table(conn, "Expenses", "expense", fields=[
        {"name": "name", "type": "text"},
    ])
    conn.execute("INSERT INTO u_expenses (created_at, updated_at, name) VALUES ('t','t','Groceries')")
    conn.commit()

    add_field(conn, table_id, {"name": "category", "type": "text"})

    row = conn.execute("SELECT name, category FROM u_expenses").fetchone()
    assert row["name"] == "Groceries"
    assert row["category"] is None  # existing row backfills NULL


def test_add_field_required_is_still_nullable_physically(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    table_id = declare_table(conn, "Expenses", "expense", fields=[
        {"name": "name", "type": "text"},
    ])
    # must not raise, even though 'required' is True -- see migrate.add_field docstring
    add_field(conn, table_id, {"name": "category", "type": "text", "required": True})
    conn.execute("INSERT INTO u_expenses (created_at, updated_at, name) VALUES ('t','t','x')")
    conn.commit()


def test_add_field_records_required_in_catalog_for_future_enforcement(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    table_id = declare_table(conn, "Expenses", "expense", fields=[{"name": "name", "type": "text"}])
    add_field(conn, table_id, {"name": "category", "type": "text", "required": True})
    row = conn.execute(
        "SELECT required FROM _ft_field WHERE table_id = ? AND name = 'category'", (table_id,)
    ).fetchone()
    assert row["required"] == 1


def test_add_money_field_creates_three_columns(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    table_id = declare_table(conn, "Expenses", "expense", fields=[{"name": "name", "type": "text"}])
    add_field(conn, table_id, {"name": "amount", "type": "money", "role": "amount"})

    conn.execute(
        "INSERT INTO u_expenses (created_at, updated_at, name, amount, amount_currency, amount_rate_to_base) "
        "VALUES ('t','t','Rent', 500, 'EUR', 1)"
    )
    conn.commit()
    row = conn.execute("SELECT amount, amount_currency FROM u_expenses").fetchone()
    assert row["amount"] == 500


def test_add_computed_field_via_alter_table(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    table_id = declare_table(conn, "Uni job", "income", fields=[
        {"name": "hours", "type": "number"},
        {"name": "rate", "type": "number"},
    ])
    # this proves SQLite in this environment supports adding a VIRTUAL
    # generated column via ALTER TABLE ADD COLUMN
    add_field(conn, table_id, {"name": "amount_paid", "type": "computed", "expr": "hours * rate"})

    conn.execute("INSERT INTO u_uni_job (created_at, updated_at, hours, rate) VALUES ('t','t',10,12)")
    conn.commit()
    row = conn.execute("SELECT amount_paid FROM u_uni_job").fetchone()
    assert row["amount_paid"] == 120


def test_add_field_real_cycle_across_two_existing_tables(tmp_path):
    """Unlike declare_table (last batch), add_field targets an EXISTING
    table, so a genuine multi-table cycle is now reachable end to end."""
    conn = open_env(tmp_path / "env.ftdb")
    a_id = declare_table(conn, "A", "income", fields=[{"name": "amount", "type": "money"}])
    b_id = declare_table(conn, "B", "income", fields=[{"name": "amount", "type": "money"}])

    # A's choice funds B -- fine, first edge
    add_field(conn, a_id, {"name": "type", "type": "choice", "choices": [
        {"label": "from B", "funds_table_id": b_id},
    ]})

    # B's choice funding A would close the loop A -> B -> A
    with pytest.raises(ValueError, match="cycle"):
        add_field(conn, b_id, {"name": "type", "type": "choice", "choices": [
            {"label": "from A", "funds_table_id": a_id},
        ]})


def test_add_field_unknown_table_raises_and_rolls_back(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    with pytest.raises(ValueError):
        add_field(conn, 999, {"name": "x", "type": "text"})
    count = conn.execute("SELECT COUNT(*) AS n FROM _ft_field").fetchone()["n"]
    assert count == 0


def test_change_field_type_number_to_money_preserves_matching_column(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    table_id = declare_table(conn, "Expenses", "expense", fields=[
        {"name": "name", "type": "text"},
        {"name": "amount", "type": "number"},
    ])
    conn.execute(
        "INSERT INTO u_expenses (created_at, updated_at, name, amount) VALUES ('t','t','Rent', 500)"
    )
    conn.commit()

    field_id = conn.execute(
        "SELECT id FROM _ft_field WHERE table_id = ? AND name = 'amount'", (table_id,)
    ).fetchone()["id"]

    change_field_type(conn, field_id, "money")

    row = conn.execute("SELECT name, amount, amount_currency FROM u_expenses").fetchone()
    assert row["name"] == "Rent"       # untouched column survives the rebuild
    assert row["amount"] == 500        # same column name + compatible type: data carries over
    assert row["amount_currency"] is None  # brand new column: no data to carry, starts NULL


def test_change_field_type_updates_catalog(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    table_id = declare_table(conn, "Expenses", "expense", fields=[{"name": "amount", "type": "number"}])
    field_id = conn.execute("SELECT id FROM _ft_field WHERE table_id = ?", (table_id,)).fetchone()["id"]

    change_field_type(conn, field_id, "money")

    row = conn.execute("SELECT type FROM _ft_field WHERE id = ?", (field_id,)).fetchone()
    assert row["type"] == "money"


def test_change_field_type_to_computed_requires_expr(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    table_id = declare_table(conn, "Uni job", "income", fields=[
        {"name": "hours", "type": "number"}, {"name": "rate", "type": "number"},
        {"name": "amount_paid", "type": "number"},
    ])
    field_id = conn.execute(
        "SELECT id FROM _ft_field WHERE table_id = ? AND name = 'amount_paid'", (table_id,)
    ).fetchone()["id"]

    with pytest.raises(ValueError):
        change_field_type(conn, field_id, "computed")  # no expr given

    change_field_type(conn, field_id, "computed", expr="hours * rate")
    conn.execute("INSERT INTO u_uni_job (created_at, updated_at, hours, rate) VALUES ('t','t',5,10)")
    conn.commit()
    row = conn.execute("SELECT amount_paid FROM u_uni_job").fetchone()
    assert row["amount_paid"] == 50


def test_apply_pending_migrations_noop_when_current(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    applied = apply_pending_migrations(conn)
    assert applied == []


def test_apply_pending_migrations_runs_and_records(tmp_path, monkeypatch):
    conn = open_env(tmp_path / "env.ftdb")

    def fake_v2(c):
        c.execute("INSERT INTO _ft_meta (key, value) VALUES ('migrated_marker', 'yes')")

    monkeypatch.setitem(MIGRATIONS, 2, fake_v2)
    applied = apply_pending_migrations(conn)

    assert applied == [2]
    meta = read_meta(conn)
    assert meta["schema_version"] == "2"
    assert meta["migrated_marker"] == "yes"
    row = conn.execute("SELECT applied_at FROM _ft_migration WHERE version = 2").fetchone()
    assert row is not None
