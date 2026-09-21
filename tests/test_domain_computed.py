"""Tests for app.domain.computed."""

import pytest

from app.domain.computed import is_computed, reject_write


def test_is_computed():
    assert is_computed({"type": "computed"}) is True
    assert is_computed({"type": "money"}) is False


def test_reject_write_raises_for_computed_field():
    with pytest.raises(ValueError, match="computed"):
        reject_write({"name": "amount_paid", "type": "computed"})


def test_reject_write_is_a_noop_for_normal_fields():
    reject_write({"name": "hours", "type": "number"})  # must not raise
