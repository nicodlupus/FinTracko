"""Tests for app.catalog.validate: identifier slugification and safety."""

import pytest

from app.catalog.validate import (
    slugify,
    validate_sql_identifier,
    sql_name_for_table,
    sql_name_for_field,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Uni job", "uni_job"),
        ("  Personal Income  ", "personal_income"),
        ("Uni-Tuition!!", "uni_tuition"),
        ("2026 Expenses", "_2026_expenses"),
        ("Café budget", "caf_budget"),
    ],
)
def test_slugify(raw, expected):
    assert slugify(raw) == expected


def test_slugify_rejects_all_punctuation():
    with pytest.raises(ValueError):
        slugify("!!!")


@pytest.mark.parametrize(
    "identifier",
    ["uni_job", "amount", "type", "x"],
)
def test_valid_identifiers_pass(identifier):
    validate_sql_identifier(identifier)  # must not raise


@pytest.mark.parametrize(
    "identifier",
    ["Uni_job", "1table", "drop table", "id", "deleted_at", "class"],
)
def test_invalid_or_reserved_identifiers_raise(identifier):
    with pytest.raises(ValueError):
        validate_sql_identifier(identifier)


def test_sql_name_for_table_has_u_prefix():
    assert sql_name_for_table("Uni job") == "u_uni_job"


def test_sql_name_for_field_has_no_prefix():
    assert sql_name_for_field("Amount Paid") == "amount_paid"


def test_sql_injection_attempt_is_neutralised_not_rejected():
    # slugify strips everything but a-z0-9, so an injection attempt simply
    # becomes a harmless (if ugly) identifier -- it must NOT raise, and the
    # result must contain no characters that could break out of a DDL string.
    result = sql_name_for_table("x; DROP TABLE _ft_table; --")
    assert result == "u_x_drop_table_ft_table"
    validate_sql_identifier(result[2:])  # re-affirms it is DDL-safe
