"""Bring an Ossie document to a single canonical form before mapping it.

After :func:`normalize`, the following holds:
- every ``ai_context`` is an :class:`AIContextObject`. A plain-string ``ai_context`` becomes
  ``{instructions: <string>}``; the spec describes the string form as custom instructions;
- every ontology ``iri`` written as a ``prefix:local`` QName, with a prefix declared in
  ``prefixes``, is expanded to a full IRI. ``prefixes`` is kept, so the IRIs can be compacted
  again when writing.

Names are left unchanged. Core names resolve case-insensitively; lookups that need that should
use :func:`ossie_neo4j.ossie.rules.fold`.
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from ..issues import Issue, Severity
from .models import AIContextObject, MappingDocument, Ontology, SemanticModel

Document = SemanticModel | Ontology | MappingDocument
D = TypeVar("D", SemanticModel, Ontology, MappingDocument)


def normalize(doc: D) -> tuple[D, list[Issue]]:
    out = doc.model_copy(deep=True)
    issues: list[Issue] = []
    prefixes = out.prefixes if isinstance(out, Ontology) else None
    for path, obj in _walk(out, ""):
        if isinstance(getattr(obj, "ai_context", None), str):
            obj.ai_context = AIContextObject(instructions=obj.ai_context)
        iri = getattr(obj, "iri", None) if isinstance(out, Ontology) else None
        if isinstance(iri, str):
            expanded, issue = expand_iri(iri, prefixes or {}, f"{path}.iri".lstrip("."))
            obj.iri = expanded
            if issue:
                issues.append(issue)
    return out, issues


def expand_iri(iri: str, prefixes: dict[str, str], path: str = "") -> tuple[str, Issue | None]:
    """Expand ``prefix:local`` using ``prefixes``.

    Absolute IRIs and IRIs with an unknown prefix are returned unchanged.
    """
    if _is_absolute(iri) or ":" not in iri:
        return iri, None
    prefix, local = iri.split(":", 1)
    if prefix in prefixes:
        return prefixes[prefix] + local, None
    return iri, Issue(
        "unknown_iri_prefix",
        f"prefix {prefix!r} is not declared in prefixes; left as is",
        path,
        Severity.WARNING,
    )


def compact_iri(iri: str, prefixes: dict[str, str]) -> str:
    """The inverse of :func:`expand_iri`: compact using the longest matching namespace."""
    best = max(
        ((p, ns) for p, ns in prefixes.items() if iri.startswith(ns) and len(iri) > len(ns)),
        key=lambda pn: len(pn[1]),
        default=None,
    )
    return f"{best[0]}:{iri[len(best[1]) :]}" if best else iri


def _is_absolute(iri: str) -> bool:
    return "://" in iri or iri.startswith(("urn:", "mailto:", "tag:"))


def _walk(obj: object, path: str):
    """Yield ``(path, model)`` for every Pydantic model nested in ``obj``, ``obj`` included."""
    if isinstance(obj, BaseModel):
        yield path, obj
        for name in type(obj).model_fields:
            yield from _walk(getattr(obj, name), f"{path}.{name}" if path else name)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            label = getattr(item, "name", None) or getattr(item, "concept", None) or i
            yield from _walk(item, f"{path}.{label}")
    elif isinstance(obj, dict):
        # Plain dicts (annotations, nonportable data) are opaque; we never rewrite inside them.
        return
