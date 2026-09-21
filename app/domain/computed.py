"""The one rule for computed fields: nobody writes to them directly.

A computed field's column is `GENERATED ALWAYS AS (...) VIRTUAL` -- SQLite
itself refuses a direct write to it, but with a cryptic error. This module
exists to catch the mistake earlier, with a message that says what actually
went wrong, and to keep that rule in one place rather than duplicated between
insert_entry and update_entry.
"""

from __future__ import annotations


def is_computed(field: dict) -> bool:
    return field["type"] == "computed"


def reject_write(field: dict) -> None:
    """Raise if `field` is computed. Call this before ever touching its value."""
    if is_computed(field):
        raise ValueError(
            f"{field['name']!r} is a computed field and is derived automatically; "
            "it cannot be set directly"
        )
