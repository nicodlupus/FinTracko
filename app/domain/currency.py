"""Currency conversion for money fields.

A money field stores three columns: amount, currency, rate_to_base. The rate
is looked up here and captured at write time -- once a row is written, its
amount_base is fixed forever, even if _ft_rate gets a new entry for that
currency tomorrow. This is what keeps historical totals from silently moving
when you update an exchange rate.
"""

from __future__ import annotations

import sqlite3

from app.catalog.schema import read_meta, utcnow


def base_currency(conn: sqlite3.Connection) -> str:
    return read_meta(conn)["base_currency"]


def set_rate(conn: sqlite3.Connection, code: str, rate_to_base_value: float, as_of: str | None = None) -> None:
    """Record that 1 unit of `code` was worth `rate_to_base_value` base-currency units as of `as_of`."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO _ft_rate (code, rate_to_base, as_of) VALUES (?, ?, ?)",
            (code, rate_to_base_value, as_of or utcnow()),
        )


def rate_to_base(conn: sqlite3.Connection, code: str, as_of: str | None = None) -> float:
    """1 unit of `code` in base-currency units.

    Base currency itself is always 1.0, with no lookup. Otherwise: the most
    recent known rate, or -- if `as_of` is given -- the most recent rate that
    was in effect on or before that date. Raises if nothing qualifies rather
    than silently assuming parity; a missing rate is a data problem, not a
    1:1 default.
    """
    if code == base_currency(conn):
        return 1.0

    if as_of is None:
        row = conn.execute(
            "SELECT rate_to_base FROM _ft_rate WHERE code = ? ORDER BY as_of DESC LIMIT 1", (code,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT rate_to_base FROM _ft_rate WHERE code = ? AND as_of <= ? ORDER BY as_of DESC LIMIT 1",
            (code, as_of),
        ).fetchone()

    if row is None:
        suffix = f" on or before {as_of}" if as_of else ""
        raise ValueError(f"no exchange rate on file for {code!r}{suffix}")
    return row["rate_to_base"]
