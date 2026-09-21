"""Tests for app.catalog.schema: the _ft_meta / _ft_table catalog base."""

import sqlite3

import pytest

from app.catalog.schema import open_env, read_meta


def test_fresh_file_seeds_meta_and_creates_table(tmp_path):
    conn = open_env(tmp_path / "env.ftdb", env_name="Current", env_kind="current")
    meta = read_meta(conn)

    assert meta["env_name"] == "Current"
    assert meta["env_kind"] == "current"
    assert meta["base_currency"] == "EUR"
    assert meta["schema_version"] == "1"
    assert "created_at" in meta

    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='_ft_table'"
    ).fetchall()
    assert len(tables) == 1


def test_open_env_twice_is_idempotent(tmp_path):
    path = tmp_path / "env.ftdb"
    conn1 = open_env(path)
    first_created_at = read_meta(conn1)["created_at"]
    conn1.close()

    conn2 = open_env(path)
    second_created_at = read_meta(conn2)["created_at"]

    assert first_created_at == second_created_at


def test_soft_deleted_table_name_can_be_reused(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")

    conn.execute(
        "INSERT INTO _ft_table (name, sql_name, kind, created_at) "
        "VALUES ('Uni job', 'u_uni_job', 'income', '2026-01-01T00:00:00')"
    )
    conn.commit()

    # soft-delete it
    conn.execute("UPDATE _ft_table SET deleted_at = '2026-02-01T00:00:00' WHERE name = 'Uni job'")
    conn.commit()

    # same name, new live row -- must succeed
    conn.execute(
        "INSERT INTO _ft_table (name, sql_name, kind, created_at) "
        "VALUES ('Uni job', 'u_uni_job_2', 'income', '2026-02-01T00:00:00')"
    )
    conn.commit()


def test_two_live_rows_with_same_name_raises(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")

    conn.execute(
        "INSERT INTO _ft_table (name, sql_name, kind, created_at) "
        "VALUES ('Uni job', 'u_uni_job', 'income', '2026-01-01T00:00:00')"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO _ft_table (name, sql_name, kind, created_at) "
            "VALUES ('Uni job', 'u_uni_job_2', 'income', '2026-01-01T00:00:00')"
        )


def test_newer_schema_version_refuses_to_open(tmp_path):
    path = tmp_path / "env.ftdb"
    conn = open_env(path)
    conn.execute("UPDATE _ft_meta SET value = '999' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError):
        open_env(path)
