"""The OGS rules that JSON Schema cannot express.

These are the rules on https://neo4j.com/docs/ontology-graph-spec/format/rules/ (the libraries'
"Core" validator set). Issue codes are our own, except ``missing_relation_to_node``, which the
docs give as an example.
"""

from __future__ import annotations

from ..issues import Issue, Severity, join_path
from . import READABLE_VERSIONS
from .models import (
    ConstraintType,
    GraphModel,
    NodeIndex,
    Property,
    RelationshipEnd,
    RelationshipIndex,
    RelationshipMapping,
)

_MAX_LEN = {
    "model_name": 256,
    "model_description": 65_536,
    "description": 1_024,
    "property_name": 256,
    "constraint_or_index_name": 800,
}


def check_rules(model: GraphModel) -> list[Issue]:
    issues: list[Issue] = []
    add = issues.append

    if not any(model.schema_.endswith(f"/{v}/schema.json") for v in READABLE_VERSIONS):
        add(
            Issue(
                "unknown_schema_version",
                f"schema {model.schema_!r} is not one of the supported versions "
                f"{', '.join(READABLE_VERSIONS)}; reading it as 1.1.0",
                "schema",
                Severity.WARNING,
            )
        )
    _length(add, model.name, _MAX_LEN["model_name"], "name")
    _length(add, model.description, _MAX_LEN["model_description"], "description")

    nodes = model.nodes or {}
    for node_id, node in nodes.items():
        path = join_path("nodes", node_id)
        props = node.properties or {}
        _length(add, node.description, _MAX_LEN["description"], join_path(path, "description"))
        _properties(add, props, path)
        for cid, c in (node.constraints or {}).items():
            _constraint(
                add, c.type, c.properties, c.name, props, join_path(path, "constraints", cid)
            )
        for iid, idx in ((node.extensions.indexes if node.extensions else None) or {}).items():
            _index(add, idx, props, join_path(path, "extensions.indexes", iid))

    relationships = model.relationships or {}
    for rel_id, rel in relationships.items():
        path = join_path("relationships", rel_id)
        props = rel.properties or {}
        _length(add, rel.description, _MAX_LEN["description"], join_path(path, "description"))
        _end(add, rel.from_, nodes, join_path(path, "from"), "missing_relation_from_node")
        _end(add, rel.to, nodes, join_path(path, "to"), "missing_relation_to_node")
        _properties(add, props, path)
        for cid, c in (rel.constraints or {}).items():
            _constraint(
                add, c.type, c.properties, c.name, props, join_path(path, "constraints", cid)
            )
        for iid, idx in ((rel.extensions.indexes if rel.extensions else None) or {}).items():
            _index(add, idx, props, join_path(path, "extensions.indexes", iid))

    mappings = (model.extensions.data_source_mappings if model.extensions else None) or {}
    for set_id, items in mappings.items():
        for i, m in enumerate(items):
            if isinstance(m, RelationshipMapping) and m.key:
                rel = relationships.get(m.relationship)
                allowed = set((rel.properties or {}) if rel else {})
                for k in m.key:
                    if k not in allowed:
                        add(
                            Issue(
                                "invalid_relationship_mapping_key",
                                f"key {k!r} is not a property of relationship {m.relationship!r}",
                                join_path("extensions.data_source_mappings", set_id, i, "key"),
                            )
                        )
    return issues


def _length(add, value: str | None, limit: int, path: str) -> None:
    if value is not None and len(value) > limit:
        add(Issue("too_long", f"{len(value)} characters, maximum is {limit}", path))


def _properties(add, props: dict[str, Property], owner_path: str) -> None:
    for pid, p in props.items():
        path = join_path(owner_path, "properties", pid)
        if p.pattern is not None and p.one_of is not None:
            add(Issue("pattern_and_one_of", "a property can't set both pattern and one_of", path))
        _length(add, p.description, _MAX_LEN["description"], join_path(path, "description"))
        _length(add, p.name, _MAX_LEN["property_name"], join_path(path, "name"))


def _constraint(add, ctype, properties, name, props, path) -> None:
    for p in properties:
        if p not in props:
            add(Issue("missing_constraint_property", f"no property with id {p!r}", path))
    if ctype in (ConstraintType.EXISTS, ConstraintType.PROPERTY_TYPE) and len(properties) != 1:
        add(
            Issue(
                "constraint_property_count",
                f"a {ctype} constraint must cover exactly one property, got {len(properties)}",
                path,
            )
        )
    _length(add, name, _MAX_LEN["constraint_or_index_name"], join_path(path, "name"))


def _index(add, idx: NodeIndex | RelationshipIndex, props, path) -> None:
    for p in idx.properties:
        if p not in props:
            add(Issue("missing_index_property", f"no property with id {p!r}", path))
    if idx.options is not None and idx.options.type != idx.type:
        add(
            Issue(
                "index_options_type_mismatch",
                f"options type {idx.options.type} does not match index type {idx.type}",
                join_path(path, "options"),
            )
        )
    _length(add, idx.name, _MAX_LEN["constraint_or_index_name"], join_path(path, "name"))


def _end(add, end: RelationshipEnd, nodes, path: str, missing_code: str) -> None:
    if end.node is not None and end.node not in nodes:
        add(Issue(missing_code, f"no node with id {end.node!r}", join_path(path, "node")))
    if end.count is not None and (end.min_count is not None or end.max_count is not None):
        add(
            Issue(
                "count_with_min_max", "can't set count together with min_count or max_count", path
            )
        )
    for attr in ("count", "min_count"):
        v = getattr(end, attr)
        if v is not None and v < 0:
            add(Issue("negative_count", f"{attr} must not be negative", join_path(path, attr)))
    if end.min_count is not None and end.max_count is not None and end.min_count > end.max_count:
        add(Issue("min_greater_than_max", "min_count must not be greater than max_count", path))
