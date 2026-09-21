"""Tests for _ft_computed, _ft_target, _ft_rate, _ft_audit, _ft_migration."""

import sqlite3

import pytest

from app.catalog.schema import open_env


def _make_table(conn, name="Tuition", sql_name="u_tuition", kind="obligation"):
    cur = conn.execute(
        "INSERT INTO _ft_table (name, sql_name, kind, created_at) VALUES (?, ?, ?, '2026-01-01')",
        (name, sql_name, kind),
    )
    conn.commit()
    return cur.lastrowid


def _make_field(conn, table_id, name="amount_paid", sql_name="amount_paid", type_="computed"):
    cur = conn.execute(
        "INSERT INTO _ft_field (table_id, name, sql_name, type) VALUES (?, ?, ?, ?)",
        (table_id, name, sql_name, type_),
    )
    conn.commit()
    return cur.lastrowid


def test_computed_requires_existing_field(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO _ft_computed (field_id, expr) VALUES (999, 'hours * rate')")


def test_computed_expr_stored(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    t = _make_table(conn, "Uni job", "u_uni_job", kind="income")
    f = _make_field(conn, t, "amount_paid", "amount_paid", "computed")
    conn.execute("INSERT INTO _ft_computed (field_id, expr) VALUES (?, 'hours * rate')", (f,))
    conn.commit()

    row = conn.execute("SELECT expr FROM _ft_computed WHERE field_id = ?", (f,)).fetchone()
    assert row["expr"] == "hours * rate"


def test_target_requires_existing_table(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO _ft_target (table_id, total_due, currency, as_of) "
            "VALUES (999, 5000, 'EUR', '2026-01-01')"
        )


def test_target_one_per_table(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    t = _make_table(conn)
    conn.execute(
        "INSERT INTO _ft_target (table_id, total_due, currency, as_of) VALUES (?, 5000, 'EUR', '2026-01-01')",
        (t,),
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO _ft_target (table_id, total_due, currency, as_of) VALUES (?, 6000, 'EUR', '2026-02-01')",
            (t,),
        )


def test_rate_same_code_different_dates_allowed(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    conn.execute("INSERT INTO _ft_rate (code, rate_to_base, as_of) VALUES ('USD', 0.92, '2026-01-01')")
    conn.execute("INSERT INTO _ft_rate (code, rate_to_base, as_of) VALUES ('USD', 0.90, '2026-02-01')")
    conn.commit()

    rows = conn.execute("SELECT rate_to_base FROM _ft_rate WHERE code = 'USD' ORDER BY as_of").fetchall()
    assert [r["rate_to_base"] for r in rows] == [0.92, 0.90]


def test_rate_same_code_same_date_rejected(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    conn.execute("INSERT INTO _ft_rate (code, rate_to_base, as_of) VALUES ('USD', 0.92, '2026-01-01')")
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO _ft_rate (code, rate_to_base, as_of) VALUES ('USD', 0.95, '2026-01-01')")


def test_audit_rejects_bad_op(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO _ft_audit (ts, sql_name, row_id, op) VALUES ('2026-01-01', 'u_tuition', 1, 'oops')"
        )


def test_audit_accepts_insert_update_delete(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    for op in ("insert", "update", "delete"):
        conn.execute(
            "INSERT INTO _ft_audit (ts, sql_name, row_id, op) VALUES ('2026-01-01', 'u_tuition', 1, ?)",
            (op,),
        )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) AS n FROM _ft_audit").fetchone()["n"]
    assert count == 3


def test_migration_version_unique(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    conn.execute("INSERT INTO _ft_migration (version, applied_at) VALUES (1, '2026-01-01')")
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO _ft_migration (version, applied_at) VALUES (1, '2026-01-02')")
