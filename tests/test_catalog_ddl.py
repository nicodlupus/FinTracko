"""Tests for app.catalog.ddl: catalog declaration -> real u_* tables."""

import sqlite3

import pytest

from app.catalog.schema import open_env
from app.catalog.ddl import declare_table, build_create_table_sql, would_create_cycle


def test_build_create_table_sql_covers_every_field_type():
    sql = build_create_table_sql(
        "u_demo",
        [
            {"sql_name": "d", "type": "date", "required": False},
            {"sql_name": "t", "type": "text", "required": True},
            {"sql_name": "n", "type": "number", "required": False},
            {"sql_name": "c", "type": "choice", "required": False},
            {"sql_name": "m", "type": "money", "required": True},
            {"sql_name": "x", "type": "computed", "required": False, "expr": "n * 2"},
        ],
    )
    assert "id INTEGER PRIMARY KEY" in sql
    assert "d TEXT" in sql
    assert "t TEXT NOT NULL" in sql
    assert "n REAL" in sql
    assert "c TEXT" in sql
    assert "m REAL NOT NULL" in sql
    assert "m_currency TEXT NOT NULL" in sql
    assert "m_rate_to_base REAL NOT NULL" in sql
    assert "x REAL GENERATED ALWAYS AS (n * 2) VIRTUAL" in sql


def test_declare_simple_income_table(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    table_id = declare_table(
        conn, "Personal Income", "income",
        fields=[
            {"name": "Source", "type": "text"},
            {"name": "Date", "type": "date", "role": "date"},
            {"name": "Amount", "type": "money", "role": "amount", "required": True},
        ],
    )
    assert table_id is not None

    row = conn.execute("SELECT sql_name FROM _ft_table WHERE id = ?", (table_id,)).fetchone()
    assert row["sql_name"] == "u_personal_income"

    conn.execute(
        "INSERT INTO u_personal_income (created_at, updated_at, source, date, amount, "
        "amount_currency, amount_rate_to_base) VALUES ('t','t','Freelance','2026-01-01',100,'EUR',1)"
    )
    conn.commit()
    got = conn.execute("SELECT source, amount, amount_currency FROM u_personal_income").fetchone()
    assert got["source"] == "Freelance"
    assert got["amount"] == 100
    assert got["amount_currency"] == "EUR"


def test_computed_field_generates_value(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    declare_table(
        conn, "Uni job", "income",
        fields=[
            {"name": "hours", "type": "number"},
            {"name": "rate", "type": "number"},
            {"name": "amount_paid", "type": "computed", "role": "amount", "expr": "hours * rate"},
        ],
    )
    conn.execute("INSERT INTO u_uni_job (created_at, updated_at, hours, rate) VALUES ('t','t',10,12)")
    conn.commit()
    row = conn.execute("SELECT amount_paid FROM u_uni_job").fetchone()
    assert row["amount_paid"] == 120


def test_choice_with_funding_link(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    job_id = declare_table(conn, "Uni job", "income", fields=[
        {"name": "hours", "type": "number"},
    ])
    declare_table(conn, "Tuition", "obligation", fields=[
        {"name": "amount", "type": "money", "role": "amount"},
        {"name": "type", "type": "choice", "role": "choice", "choices": [
            {"label": "uni work", "funds_table_id": job_id},
            {"label": "me", "funds_table_id": None},
        ]},
    ])

    row = conn.execute(
        "SELECT funds_table_id FROM _ft_choice WHERE label = 'uni work'"
    ).fetchone()
    assert row["funds_table_id"] == job_id


def test_direct_self_reference_rejected(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    a_id = declare_table(conn, "A", "income", fields=[{"name": "amount", "type": "money"}])
    assert would_create_cycle(conn, a_id, a_id) is True


def test_two_table_cycle_rejected(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    a_id = declare_table(conn, "A", "income", fields=[
        {"name": "amount", "type": "money"},
    ])
    b_id = declare_table(conn, "B", "income", fields=[
        {"name": "amount", "type": "money"},
        {"name": "type", "type": "choice", "choices": [
            {"label": "from A", "funds_table_id": a_id},
        ]},
    ])
    # A -> ... nothing yet; B -> A already exists. Now try A's choice -> B:
    # that would complete a cycle A -> B -> A.
    assert would_create_cycle(conn, a_id, b_id) is True


def test_unknown_kind_rejected(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    with pytest.raises(ValueError):
        declare_table(conn, "Weird", "not_a_real_kind", fields=[])


def test_failed_declare_is_fully_rolled_back(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    with pytest.raises(ValueError):
        declare_table(conn, "Broken", "income", fields=[
            {"name": "x", "type": "not_a_real_type"},
        ])
    count = conn.execute("SELECT COUNT(*) AS n FROM _ft_table").fetchone()["n"]
    assert count == 0
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='u_broken'"
    ).fetchall()
    assert tables == []
