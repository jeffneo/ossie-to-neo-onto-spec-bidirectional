"""Read Ossie 0.1.1 (still named OSI at the time) by migrating it to 0.3.0.dev0.

What changed between the two versions:

- 0.1.1 wraps models in a root ``semantic_model: [...]`` list. 0.3.0 has one model per
  document.
- 0.1.1 has root ``dialects`` and ``vendors`` lists. They are informational only and dropped.
- 0.1.1 expressions are ``dialects`` only. When every dialect gives the same plain column name
  (as nearly all 0.1.1 fields do, e.g. ``ANSI_SQL: ss_sold_date_sk``), that name becomes the
  portable ``sql``, because an identifier means the same in every dialect. Every other
  expression is kept as ``dialects``.
- 0.1.1 ``custom_extensions`` carry ``data`` as a JSON *string*. They become
  ``nonportable_extensions`` with object ``data``. A value that isn't a JSON object is wrapped
  as ``{"value": ...}``, and a string that isn't JSON as ``{"raw": "..."}``.

- In 0.1.1, ``primary_key``, ``unique_keys`` and relationship ``from_columns`` /
  ``to_columns`` name *source columns*. In 0.3.0 they name *fields* (upstream PR #486). Each
  such name is mapped to the field over that column. If no field exists, one is added
  (``{name: col, expression: {sql: col}}``). The upstream 0.1.1 tpcds example needs this:
  it keys ``store_sales`` on ``ss_ticket_number``, which is a column but not a field.

0.1.1 has no ``datatype`` and no dataset-scoped metrics, so nothing needs translating there.
"""

from __future__ import annotations

import copy
import json
from typing import Any

from ..issues import Issue, Severity, join_path
from . import SPEC_VERSION

LEGACY_VERSIONS = ("0.1.0", "0.1.1")


def is_legacy(doc: dict[str, Any]) -> bool:
    """True for a 0.1.x document: a root ``semantic_model`` list and no ``datasets``."""
    return isinstance(doc.get("semantic_model"), list) and "datasets" not in doc


def migrate(doc: dict[str, Any]) -> tuple[list[dict[str, Any]], list[Issue]]:
    """Migrate a 0.1.x document to one 0.3.0.dev0 semantic model document per model."""
    issues: list[Issue] = []
    for key in ("dialects", "vendors"):
        if doc.get(key):
            issues.append(
                Issue(
                    "migration_dropped_root_list",
                    f"0.1.1 root {key!r} list is informational and has no 0.3.0 equivalent",
                    key,
                    Severity.INFO,
                )
            )
    out = []
    for i, model in enumerate(doc.get("semantic_model") or []):
        out.append(_model(copy.deepcopy(model), f"semantic_model.{i}", issues))
    return out, issues


def _model(m: dict, path: str, issues: list[Issue]) -> dict:
    new: dict[str, Any] = {"version": SPEC_VERSION}
    new.update(m)
    _extensions(new, path, issues)
    for d in new.get("datasets") or []:
        dpath = join_path(path, "datasets", d.get("name"))
        _extensions(d, dpath, issues)
        for f in d.get("fields") or []:
            fpath = join_path(dpath, "fields", f.get("name"))
            _extensions(f, fpath, issues)
            _expression(f, fpath, issues)
    datasets = {(d.get("name") or "").casefold(): d for d in new.get("datasets") or []}
    for d in new.get("datasets") or []:
        dpath = join_path(path, "datasets", d.get("name"))
        if d.get("primary_key"):
            d["primary_key"] = [_field_for(d, c, dpath, issues) for c in d["primary_key"]]
        if d.get("unique_keys"):
            d["unique_keys"] = [
                [_field_for(d, c, dpath, issues) for c in key] for key in d["unique_keys"]
            ]
    for r in new.get("relationships") or []:
        rpath = join_path(path, "relationships", r.get("name"))
        _extensions(r, rpath, issues)
        for end, cols in (("from", "from_columns"), ("to", "to_columns")):
            d = datasets.get((r.get(end) or "").casefold())
            if d is not None and r.get(cols):
                r[cols] = [_field_for(d, c, rpath, issues) for c in r[cols]]
    for mt in new.get("metrics") or []:
        mpath = join_path(path, "metrics", mt.get("name"))
        _extensions(mt, mpath, issues)
    return new


def _field_for(d: dict, column: str, path: str, issues: list[Issue]) -> str:
    """The name of ``d``'s field over source column ``column``. Adds the field if missing."""
    from ..convert.naming import parse_identifier

    fields = d.setdefault("fields", [])
    folded = column.casefold()
    for f in fields:
        if f.get("name", "").casefold() == folded:
            return f["name"]
    for f in fields:
        sql = (f.get("expression") or {}).get("sql")
        if parse_identifier(sql) and parse_identifier(sql).casefold() == folded:
            return f["name"]
    fields.append({"name": column, "expression": {"sql": column}})
    issues.append(
        Issue(
            "migration_added_key_field",
            f"0.1.1 keys name source columns; added field {column!r} so the key resolves",
            path,
            Severity.INFO,
        )
    )
    return column


def _expression(f: dict, path: str, issues: list[Issue]) -> None:
    from ..convert.naming import parse_identifier

    dialects = (f.get("expression") or {}).get("dialects") or []
    names = {parse_identifier(d.get("expression")) for d in dialects}
    if len(names) == 1 and None not in names:
        f["expression"] = {"sql": next(iter(names))}
        issues.append(
            Issue(
                "migration_promoted_expression",
                "every dialect gives the same column name; it became the portable `sql`",
                join_path(path, "expression"),
                Severity.INFO,
            )
        )


def _extensions(obj: dict, path: str, issues: list[Issue]) -> None:
    exts = obj.pop("custom_extensions", None)
    if not exts:
        return
    converted = []
    for e in exts:
        raw = e.get("data")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            data = {"raw": raw}
            issues.append(
                Issue(
                    "migration_extension_not_json",
                    f"{e.get('vendor_name')} extension data is not JSON; kept as {{'raw': ...}}",
                    path,
                    Severity.WARNING,
                )
            )
        if not isinstance(data, dict):
            data = {"value": data}
        converted.append({"vendor_name": e.get("vendor_name"), "data": data})
    obj["nonportable_extensions"] = converted
