"""Tests for the _ft_field / _ft_choice layer of the catalog."""

import sqlite3

import pytest

from app.catalog.schema import open_env


def _make_table(conn, name="Uni job", sql_name="u_uni_job", kind="income"):
    cur = conn.execute(
        "INSERT INTO _ft_table (name, sql_name, kind, created_at) VALUES (?, ?, ?, '2026-01-01')",
        (name, sql_name, kind),
    )
    conn.commit()
    return cur.lastrowid


def _make_field(conn, table_id, name, sql_name, type_, role="none"):
    cur = conn.execute(
        "INSERT INTO _ft_field (table_id, name, sql_name, type, role) VALUES (?, ?, ?, ?, ?)",
        (table_id, name, sql_name, type_, role),
    )
    conn.commit()
    return cur.lastrowid


def test_field_requires_existing_table(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO _ft_field (table_id, name, sql_name, type) VALUES (999, 'x', 'x', 'text')"
        )


def test_field_name_unique_within_table_only(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    t1 = _make_table(conn, "Uni job", "u_uni_job")
    t2 = _make_table(conn, "Expenses", "u_expenses", kind="expense")

    _make_field(conn, t1, "date", "date", "date", role="date")
    # same field name in a *different* table must be fine
    _make_field(conn, t2, "date", "date", "date", role="date")

    with pytest.raises(sqlite3.IntegrityError):
        _make_field(conn, t1, "date", "date_2", "date")


def test_only_one_amount_role_per_table(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    t1 = _make_table(conn)
    _make_field(conn, t1, "amount_paid", "amount_paid", "money", role="amount")

    with pytest.raises(sqlite3.IntegrityError):
        _make_field(conn, t1, "bonus", "bonus", "money", role="amount")


def test_only_one_date_role_per_table(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    t1 = _make_table(conn)
    _make_field(conn, t1, "date", "date", "date", role="date")

    with pytest.raises(sqlite3.IntegrityError):
        _make_field(conn, t1, "paid_on", "paid_on", "date", role="date")


def test_choice_requires_existing_field(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO _ft_choice (field_id, label) VALUES (999, 'grant')"
        )


def test_choice_label_unique_within_field(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    t1 = _make_table(conn, "Tuition", "u_tuition", kind="obligation")
    f1 = _make_field(conn, t1, "type", "type", "choice", role="choice")

    conn.execute("INSERT INTO _ft_choice (field_id, label) VALUES (?, 'uni work')", (f1,))
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO _ft_choice (field_id, label) VALUES (?, 'uni work')", (f1,))


def test_choice_can_fund_another_table(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    job = _make_table(conn, "Uni job", "u_uni_job")
    tuition = _make_table(conn, "Tuition", "u_tuition", kind="obligation")
    type_field = _make_field(conn, tuition, "type", "type", "choice", role="choice")

    conn.execute(
        "INSERT INTO _ft_choice (field_id, label, funds_table_id) VALUES (?, 'uni work', ?)",
        (type_field, job),
    )
    conn.commit()

    row = conn.execute(
        "SELECT funds_table_id FROM _ft_choice WHERE label = 'uni work'"
    ).fetchone()
    assert row["funds_table_id"] == job
