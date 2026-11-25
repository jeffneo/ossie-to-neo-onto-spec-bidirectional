"""Name conversions between the conventions of the two specs.

- OGS ids are free strings, conventionally kebab-case (for example ``acted-in``).
- OGS labels are PascalCase and relationship types UPPER_SNAKE_CASE.
- Ossie concept names are PascalCase and relationship names snake_case.
"""

from __future__ import annotations

import re
from collections.abc import Container

_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|\b|_)|[A-Z]?[a-z]+|[A-Z]+|\d+")


def words(s: str) -> list[str]:
    return _WORD.findall(s.replace("-", " ").replace("_", " "))


def snake(s: str) -> str:
    return "_".join(w.lower() for w in words(s))


def kebab(s: str) -> str:
    return "-".join(w.lower() for w in words(s))


def upper_snake(s: str) -> str:
    return "_".join(w.upper() for w in words(s))


def pascal(s: str) -> str:
    return "".join(
        w[:1].upper() + w[1:].lower() if w.isupper() else w[:1].upper() + w[1:] for w in words(s)
    )


def phrase(s: str) -> str:
    """``ACTED_IN`` or ``person_id`` becomes ``acted in`` / ``person id``, for verbalizations."""
    return " ".join(w.lower() for w in words(s))


def unique(base: str, taken: Container[str]) -> str:
    """``base``, or ``base_2``, ``base_3``, … if it is already taken."""
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


_SIMPLE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_QUOTED_IDENT = re.compile(r'^"((?:[^"]|"")+)"$')


def sql_identifier(name: str) -> str:
    """A column name as an SQL identifier, double-quoted if it isn't a plain identifier."""
    if _SIMPLE_IDENT.match(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def parse_identifier(sql: str | None) -> str | None:
    """The column name if ``sql`` is a bare or double-quoted identifier, else None."""
    if sql is None:
        return None
    s = sql.strip()
    if _SIMPLE_IDENT.match(s):
        return s
    m = _QUOTED_IDENT.match(s)
    return m.group(1).replace('""', '"') if m else None
