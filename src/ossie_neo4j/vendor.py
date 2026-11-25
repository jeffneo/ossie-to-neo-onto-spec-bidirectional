"""Validator for ``NEO4J`` entries in ``nonportable_extensions``.

It is registered under the ``ossie.vendor_validators`` entry point as ``NEO4J``, so the Ossie
validation script can check our payloads:

    uv run --with apache-ossie-neo4j validation/validate.py --vendor NEO4J model.yaml

See ``extensions/vendors.md`` in apache/ossie for the protocol. ``check(payload, site)``
returns a list of findings. A plain string is an error; a dict with ``severity: warning`` is
a warning.
"""

from __future__ import annotations

from typing import Any

from .ogs.models import Neo4jType

# The keys ossie-neo4j writes at each site, with the type each value must have.
_KEYS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "field": {
        "type": str,
        "size": int,
        "nullable": bool,
        "dimension": int,
        "supported": list,
        "suggested": (str, type(None)),
        "unnamed": bool,
        "custom": dict,
    },
    "dataset": {"table_id": str, "custom": dict, "primary_keys": list, "foreign_keys": dict},
    "relationship": {
        "id": str,
        "implicit_columns": bool,
        "custom": dict,
        "references_custom": dict,
    },
    "model": {
        "id": str,
        "version": str,
        "custom": dict,
        "unnamed": bool,
        "data_source_mappings": dict,
    },
    "mapping": {"sets": dict},
}
_NEO4J_TYPES = {t.value for t in Neo4jType}


def check(payload: dict[str, Any], site: Any) -> list[Any]:
    kind = getattr(site, "kind", None)
    allowed = _KEYS.get(kind)
    if allowed is None:
        return [{"message": f"ossie-neo4j writes no NEO4J data on a {kind}", "severity": "warning"}]
    findings: list[Any] = []
    for key, value in payload.items():
        if key not in allowed:
            findings.append({"message": f"unknown NEO4J key {key!r}", "severity": "warning"})
            continue
        expected = allowed[key]
        # bool is a subclass of int; don't accept True as a size.
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            findings.append(f"NEO4J {key!r} has the wrong type ({type(value).__name__})")
    if kind == "field":
        suggested = payload.get("suggested")
        if isinstance(suggested, str) and suggested not in _NEO4J_TYPES:
            findings.append(f"NEO4J 'suggested' is not a Neo4j type: {suggested!r}")
        for t in payload.get("supported") or []:
            if t not in _NEO4J_TYPES:
                findings.append(f"NEO4J 'supported' lists an unknown Neo4j type: {t!r}")
    return findings
