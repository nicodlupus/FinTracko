"""Tests for app.domain.currency."""

import pytest

from app.catalog.schema import open_env
from app.domain.currency import base_currency, set_rate, rate_to_base


def test_base_currency_defaults_to_eur(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    assert base_currency(conn) == "EUR"


def test_base_currency_is_always_rate_1(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    assert rate_to_base(conn, "EUR") == 1.0


def test_unknown_currency_raises(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    with pytest.raises(ValueError, match="no exchange rate"):
        rate_to_base(conn, "USD")


def test_latest_rate_used_when_no_as_of_given(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    set_rate(conn, "USD", 0.90, as_of="2026-01-01")
    set_rate(conn, "USD", 0.95, as_of="2026-03-01")
    assert rate_to_base(conn, "USD") == 0.95


def test_historical_rate_used_when_as_of_given(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    set_rate(conn, "USD", 0.90, as_of="2026-01-01")
    set_rate(conn, "USD", 0.95, as_of="2026-03-01")
    assert rate_to_base(conn, "USD", as_of="2026-02-01") == 0.90


def test_as_of_before_any_rate_raises(tmp_path):
    conn = open_env(tmp_path / "env.ftdb")
    set_rate(conn, "USD", 0.90, as_of="2026-03-01")
    with pytest.raises(ValueError, match="on or before"):
        rate_to_base(conn, "USD", as_of="2026-01-01")
