"""Build a fixture .ftdb with the plan's own example data.

Run this to get something real for dbt to point at during development:

    .venv/bin/python scripts/seed_fixture.py

Writes data/environments/current.ftdb (gitignored -- this is local dev data,
never committed). Safe to delete and re-run any time.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.catalog.schema import open_env
from app.catalog.ddl import declare_table
from app.domain.entries import insert_entry

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "environments" / "current.ftdb"


def build(path: Path) -> None:
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = open_env(path, env_name="Current", env_kind="current", base_currency="EUR")

    job_id = declare_table(conn, "Uni job", "income", fields=[
        {"name": "Date", "type": "date", "role": "date"},
        {"name": "Day", "type": "text"},
        {"name": "Hours", "type": "number"},
        {"name": "Rate", "type": "number", "required": True},
        {"name": "Amount Paid", "type": "computed", "role": "amount", "expr": "hours * rate"},
    ])

    tuition_id = declare_table(conn, "Uni Tuition", "obligation", fields=[
        {"name": "Date", "type": "date", "role": "date"},
        {"name": "Amount", "type": "money", "role": "amount", "required": True},
        {"name": "Type", "type": "choice", "role": "choice", "choices": [
            {"label": "uni work", "funds_table_id": job_id},
            {"label": "grant", "funds_table_id": None},
            {"label": "me", "funds_table_id": None},
        ]},
    ])

    income_id = declare_table(conn, "Personal Income", "income", fields=[
        {"name": "Source", "type": "text"},
        {"name": "Date", "type": "date", "role": "date"},
        {"name": "Amount", "type": "money", "role": "amount", "required": True},
    ])

    expenses_id = declare_table(conn, "Expenses", "expense", fields=[
        {"name": "Amount", "type": "money", "role": "amount", "required": True},
        {"name": "Name", "type": "text", "required": True},
        {"name": "Category", "type": "text"},
        {"name": "Date", "type": "date", "role": "date"},
    ])

    conn.execute(
        "INSERT INTO _ft_target (table_id, total_due, currency, as_of) VALUES (?, 5000, 'EUR', ?)",
        (tuition_id, "2026-01-01"),
    )
    conn.commit()

    insert_entry(conn, job_id, {"Date": "2026-03-01", "Day": "Monday", "Hours": 10, "Rate": 12})
    insert_entry(conn, job_id, {"Date": "2026-03-08", "Day": "Monday", "Hours": 8, "Rate": 12})

    insert_entry(conn, tuition_id, {"Date": "2026-03-02", "Amount": 100, "Type": "uni work"})

    insert_entry(conn, income_id, {"Source": "Freelance", "Date": "2026-03-05", "Amount": 200})

    insert_entry(conn, expenses_id, {"Amount": 45.5, "Name": "Groceries", "Category": "Food", "Date": "2026-03-03"})
    insert_entry(conn, expenses_id, {"Amount": 15, "Name": "Bus pass", "Category": "Transport", "Date": "2026-03-04"})

    conn.close()
    print(f"wrote {path}")


if __name__ == "__main__":
    build(DB_PATH)
