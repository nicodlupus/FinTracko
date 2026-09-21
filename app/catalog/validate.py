"""Identifier validation and slugification.

No user-supplied text ever reaches a CREATE TABLE / column name directly --
it goes through here first. This is the boundary that keeps DDL generation
(app/catalog/ddl.py) safe: the DDL builder only ever formats strings that
already passed `slugify` + `validate_sql_identifier`.
"""

from __future__ import annotations

import re
import keyword

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_VALID_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# names that would collide with catalog internals or SQLite/Python keywords
_RESERVED = {"id", "created_at", "updated_at", "deleted_at", "rowid"}


def slugify(name: str) -> str:
    """Turn a user-facing name into a lowercase snake_case identifier.

    'Uni job' -> 'uni_job'. Leading/trailing/duplicate separators collapse.
    Raises if the result would be empty (e.g. name was all punctuation).
    """
    slug = _SLUG_RE.sub("_", name.strip().lower()).strip("_")
    if not slug:
        raise ValueError(f"name {name!r} has no usable characters for an identifier")
    if slug[0].isdigit():
        slug = f"_{slug}"
    return slug


def validate_sql_identifier(identifier: str) -> None:
    """Raise ValueError if `identifier` is not safe to interpolate into DDL.

    Deliberately strict: lowercase ascii, digits, underscore only, must start
    with a letter. This is what makes string-formatted DDL safe here --
    SQLite has no parameterised way to name a table or column, so the
    identifier itself must be provably safe before it is ever formatted in.
    """
    if not _VALID_IDENTIFIER_RE.match(identifier):
        raise ValueError(
            f"{identifier!r} is not a valid identifier "
            "(must be lowercase, start with a letter, only a-z0-9_)"
        )
    if keyword.iskeyword(identifier):
        raise ValueError(f"{identifier!r} is a Python keyword, choose another name")
    if identifier in _RESERVED:
        raise ValueError(f"{identifier!r} is reserved by the catalog, choose another name")


def sql_name_for_table(display_name: str) -> str:
    """'Uni job' -> 'u_uni_job'. The `u_` prefix keeps user tables visually
    distinct from `_ft_*` catalog tables and avoids any possible collision.
    """
    slug = slugify(display_name)
    sql_name = f"u_{slug}"
    validate_sql_identifier(sql_name)
    return sql_name


def sql_name_for_field(display_name: str) -> str:
    slug = slugify(display_name)
    validate_sql_identifier(slug)
    return slug
