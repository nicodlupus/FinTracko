"""Tests for app.domain.entries: insert/update/soft-delete/read, and audit."""

import json

import pytest

from app.catalog.schema import open_env
from app.catalog.ddl import declare_table
from app.domain.currency import set_rate
from app.domain.entries import (
    insert_entry,
    update_entry,
    soft_delete_entry,
    get_entry,
    list_entries,
)


def _uni_job(conn):
    return declare_table(conn, "Uni job", "income", fields=[
        {"name": "Date", "type": "date", "role": "date"},
        {"name": "Day", "type": "text"},
        {"name": "Hours", "type": "number"},
        {"name": "Rate", "type": "number", "required": True},
        {"name": "Amount Paid", "type": "computed", "role": "amount", "expr": "hours * rate"},
    ])


def _tuition(conn, job_id):
    return declare_table(conn, "Uni Tuition", "obligation", fields=[
        {"name": "Date", "type": "date", "role": "date"},
        {"name": "Amount", "type": "money", "role": "amount", "required": True},
        {"name": "Type", "type": "choice", "role": "choice", "choices": [
            {"label": "uni work", "funds_table_id": job_id},
            {"label": "me", "funds_table_id": None},
        ]},
    ])


def _expenses(conn):
    return declare_table(conn, "Expenses", "expense", fields=[
        {"name": "Amount", "type": "money", "role": "amount", "required": True},
        {"name": "Name", "type": "text", "required": True},
        {"name": "Category", "type": "text"},
        {"name": "Date", "type": "date", "role": "date"},
    ])


def test_insert_basic_row_with_computed_amount(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    job_id = _uni_job(conn)

    row_id = insert_entry(conn, job_id, {
        "Date": "2026-03-01", "Day": "Monday", "Hours": 10, "Rate": 12,
    })

    row = get_entry(conn, job_id, row_id)
    assert row["hours"] == 10
    assert row["amount_paid"] == 120  # hours * rate, generated -- never supplied


def test_insert_money_field_defaults_to_base_currency(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    row_id = insert_entry(conn, exp_id, {"Amount": 45.5, "Name": "Groceries"})
    row = get_entry(conn, exp_id, row_id)
    assert row["amount"] == 45.5
    assert row["amount_currency"] == "EUR"
    assert row["amount_rate_to_base"] == 1.0


def test_insert_money_field_with_explicit_currency(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    set_rate(conn, "USD", 0.92)
    row_id = insert_entry(conn, exp_id, {"Amount": {"amount": 100, "currency": "USD"}, "Name": "Textbook"})
    row = get_entry(conn, exp_id, row_id)
    assert row["amount"] == 100
    assert row["amount_currency"] == "USD"
    assert row["amount_rate_to_base"] == 0.92


def test_insert_uses_historical_rate_from_entry_date(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    set_rate(conn, "USD", 0.90, as_of="2026-01-01")
    set_rate(conn, "USD", 0.95, as_of="2026-03-01")

    row_id = insert_entry(conn, exp_id, {
        "Amount": {"amount": 100, "currency": "USD"}, "Name": "Old purchase", "Date": "2026-02-01",
    })
    row = get_entry(conn, exp_id, row_id)
    assert row["amount_rate_to_base"] == 0.90  # the rate in effect on 2026-02-01, not the latest


def test_missing_required_field_raises_and_nothing_written(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    with pytest.raises(ValueError, match="required"):
        insert_entry(conn, exp_id, {"Name": "Groceries"})  # Amount missing
    assert list_entries(conn, exp_id) == []


def test_unknown_field_name_raises(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    with pytest.raises(ValueError, match="unknown"):
        insert_entry(conn, exp_id, {"Amount": 10, "Name": "x", "Typo": "oops"})


def test_computed_field_cannot_be_set_directly(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    job_id = _uni_job(conn)
    with pytest.raises(ValueError, match="computed"):
        insert_entry(conn, job_id, {"Hours": 1, "Rate": 1, "Amount Paid": 999})


def test_choice_field_validates_against_catalog(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    job_id = _uni_job(conn)
    tuition_id = _tuition(conn, job_id)

    with pytest.raises(ValueError, match="not a valid choice"):
        insert_entry(conn, tuition_id, {"Amount": 100, "Type": "scholarship"})

    row_id = insert_entry(conn, tuition_id, {"Amount": 100, "Type": "uni work"})
    row = get_entry(conn, tuition_id, row_id)
    assert row["type"] == "uni work"


def test_the_user_scenario_end_to_end(tmp_path):
    """10h x EUR12 in uni job, then a EUR100 tuition payment typed 'uni work'.
    entries.py only writes the rows correctly here -- balance arithmetic is
    the modelling layer's job (Phase 3+), not this module's."""
    conn = open_env(tmp_path / "env.ftdb")
    job_id = _uni_job(conn)
    tuition_id = _tuition(conn, job_id)

    job_row_id = insert_entry(conn, job_id, {"Date": "2026-03-01", "Hours": 10, "Rate": 12})
    tuition_row_id = insert_entry(conn, tuition_id, {"Date": "2026-03-02", "Amount": 100, "Type": "uni work"})

    job_row = get_entry(conn, job_id, job_row_id)
    tuition_row = get_entry(conn, tuition_id, tuition_row_id)
    assert job_row["amount_paid"] == 120
    assert tuition_row["amount"] == 100
    assert tuition_row["type"] == "uni work"


def test_update_partial_fields_leaves_others_untouched(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    row_id = insert_entry(conn, exp_id, {"Amount": 10, "Name": "Groceries", "Category": "Food"})

    update_entry(conn, exp_id, row_id, {"Amount": 15})

    row = get_entry(conn, exp_id, row_id)
    assert row["amount"] == 15
    assert row["name"] == "Groceries"
    assert row["category"] == "Food"


def test_update_cannot_clear_required_field(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    row_id = insert_entry(conn, exp_id, {"Amount": 10, "Name": "Groceries"})
    with pytest.raises(ValueError, match="required"):
        update_entry(conn, exp_id, row_id, {"Name": None})


def test_update_unknown_row_raises(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    with pytest.raises(ValueError):
        update_entry(conn, exp_id, 999, {"Amount": 1})


def test_soft_delete_hides_from_list_but_keeps_the_row(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    row_id = insert_entry(conn, exp_id, {"Amount": 10, "Name": "Groceries"})

    soft_delete_entry(conn, exp_id, row_id)

    assert list_entries(conn, exp_id) == []
    assert get_entry(conn, exp_id, row_id) is None
    still_there = get_entry(conn, exp_id, row_id, include_deleted=True)
    assert still_there is not None
    assert still_there["deleted_at"] is not None


def test_double_delete_raises(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    row_id = insert_entry(conn, exp_id, {"Amount": 10, "Name": "Groceries"})
    soft_delete_entry(conn, exp_id, row_id)
    with pytest.raises(ValueError):
        soft_delete_entry(conn, exp_id, row_id)


def test_list_entries_include_deleted(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    a = insert_entry(conn, exp_id, {"Amount": 1, "Name": "a"})
    b = insert_entry(conn, exp_id, {"Amount": 2, "Name": "b"})
    soft_delete_entry(conn, exp_id, a)

    assert [r["name"] for r in list_entries(conn, exp_id)] == ["b"]
    assert {r["name"] for r in list_entries(conn, exp_id, include_deleted=True)} == {"a", "b"}


def test_insert_writes_audit_row(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    row_id = insert_entry(conn, exp_id, {"Amount": 10, "Name": "Groceries"})

    audit = conn.execute(
        "SELECT * FROM _ft_audit WHERE sql_name = 'u_expenses' AND row_id = ?", (row_id,)
    ).fetchone()
    assert audit["op"] == "insert"
    assert audit["before_json"] is None
    after = json.loads(audit["after_json"])
    assert after["name"] == "Groceries"


def test_update_and_delete_write_audit_rows_with_before_and_after(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    exp_id = _expenses(conn)
    row_id = insert_entry(conn, exp_id, {"Amount": 10, "Name": "Groceries"})
    update_entry(conn, exp_id, row_id, {"Amount": 20})
    soft_delete_entry(conn, exp_id, row_id)

    rows = conn.execute(
        "SELECT op, before_json, after_json FROM _ft_audit WHERE sql_name = 'u_expenses' "
        "AND row_id = ? ORDER BY id", (row_id,)
    ).fetchall()
    ops = [r["op"] for r in rows]
    assert ops == ["insert", "update", "delete"]

    update_row = rows[1]
    assert json.loads(update_row["before_json"])["amount"] == 10
    assert json.loads(update_row["after_json"])["amount"] == 20

    delete_row = rows[2]
    assert json.loads(delete_row["after_json"])["deleted_at"] is not None
