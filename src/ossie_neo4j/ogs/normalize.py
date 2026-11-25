"""Bring an OGS model to its single canonical (longhand) form before mapping it.

After :func:`normalize`, the following holds:
- every node's identifying label is in ``labels.identifier`` (``label`` is never set);
- no property sets ``must_exist``, ``unique`` or ``key``; each ``true`` flag has become the
  equivalent entry in ``constraints``;
- ``null`` values are absent. The models already treat them as ``None``.

This matches what the official JVM and JS libraries do when they read a model ("Reading expands
any shorthand").
"""

from __future__ import annotations

from ..issues import Issue, join_path
from .models import Constraint, ConstraintType, GraphModel, Labels, Property

# (flag attribute, constraint type, id prefix, name suffix). The id and name patterns follow the
# spec's "Shorthand and longhand" example: exists_person_name / name_Person_exists, and
# unique_movie_title / title_Movie_uniq. The spec gives no example for KEY, so "key" is our own
# choice of suffix.
_FLAGS = (
    ("must_exist", ConstraintType.EXISTS, "exists", "exists"),
    ("unique", ConstraintType.UNIQUE, "unique", "uniq"),
    ("key", ConstraintType.KEY, "key", "key"),
)


def normalize(model: GraphModel) -> tuple[GraphModel, list[Issue]]:
    """Return a normalized deep copy of ``model``, plus any issues found along the way."""
    out = model.model_copy(deep=True)
    issues: list[Issue] = []

    for node_id, node in (out.nodes or {}).items():
        path = join_path("nodes", node_id)
        if node.label is not None:
            if node.labels is None:
                node.labels = Labels()
            if node.labels.identifier is None:
                node.labels.identifier = node.label
            elif node.labels.identifier != node.label:
                issues.append(
                    Issue(
                        "conflicting_identifier_label",
                        f"label {node.label!r} and labels.identifier {node.labels.identifier!r} "
                        "differ; keeping labels.identifier",
                        path,
                    )
                )
            node.label = None
        label = node.labels.identifier if node.labels else None
        node.constraints = _expand(node_id, label, node.properties, node.constraints)

    for rel_id, rel in (out.relationships or {}).items():
        rel.constraints = _expand(rel_id, rel.type, rel.properties, rel.constraints)

    return out, issues


def _expand(
    owner_id: str,
    label: str | None,
    properties: dict[str, Property] | None,
    constraints: dict[str, Constraint] | None,
) -> dict[str, Constraint] | None:
    result = dict(constraints or {})
    for prop_id, prop in (properties or {}).items():
        for attr, ctype, id_prefix, name_suffix in _FLAGS:
            flag = getattr(prop, attr)
            setattr(prop, attr, None)
            if not flag:
                continue
            if any(c.type == ctype and c.properties == [prop_id] for c in result.values()):
                continue  # the longhand constraint is already there
            cid = _unique_key(result, f"{id_prefix}_{owner_id}_{prop_id}")
            name = f"{prop_id}_{label}_{name_suffix}" if label else None
            result[cid] = Constraint(type=ctype, properties=[prop_id], name=name)
    return result or None


def _unique_key(existing: dict[str, object], base: str) -> str:
    key, n = base, 2
    while key in existing:
        key, n = f"{base}_{n}", n + 1
    return key
