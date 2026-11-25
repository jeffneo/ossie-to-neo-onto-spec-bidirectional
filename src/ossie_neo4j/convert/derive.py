"""Core-only mode: derive a graph from an Ossie semantic model that has no ontology.

Many Ossie producers (dbt, Snowflake, ...) emit only a semantic model. To still get nodes and
relationships, this module synthesizes the two missing documents in Ossie terms. Converting all
three with the normal converter then reuses every rule of phases 1–3.

The synthesized **ontology**:
- each dataset becomes an EntityType named after it in PascalCase (``store_sales`` →
  ``StoreSales``);
- each field over a plain column becomes a single-valued attribute typed by its ``datatype``;
- the primary key becomes ``identify_by``, and a single-field key is also OneToOne;
- each relationship becomes a ManyToOne link from the ``from`` dataset (the many side) to the
  ``to`` dataset, so OGS gets ``from.max_count: 1``.

Fields used only as foreign keys (in ``from_columns`` but not in the primary key) are not
properties: in a graph, the relationship replaces them. Derived (expression) fields are not
properties either. Both are still tables' columns, so nothing is lost on the table side.

The synthesized **mapping** populates each node from its dataset's primary key and fields, and
each relationship from the foreign-key columns. That is what makes the result loadable with
Neo4j's Data Importer.
"""

from __future__ import annotations

from typing import Any

from ..ossie.models import BUILTIN_CONCEPTS, SPEC_VERSION, SemanticModel
from .mappings import column_ref
from .naming import parse_identifier, pascal, phrase, snake, unique
from .report import Loss, LossReport
from .types import ALL_GENERATED

_ROLE_FOR_DATATYPE = {
    "String": "String",
    "Integer": "Integer",
    "Decimal": "Decimal",
    "Float": "Float",
    "Boolean": "Boolean",
    "Date": "Date",
    "DateTime": "DateTime",
    "Time": "Neo4jLocalTime",
    "DateTimeTz": "Neo4jZonedDatetime",
    "Opaque": "Neo4jAny",
}


def _fold(s: str) -> str:
    return s.casefold()


def derive_ontology_and_mapping(
    sm: SemanticModel, report: LossReport
) -> tuple[dict[str, Any], dict[str, Any]]:
    """A synthesized ``(ontology, mapping)`` pair, both as plain dicts, for ``sm``."""
    taken = set(BUILTIN_CONCEPTS) | set(ALL_GENERATED)
    concept_of: dict[str, str] = {}  # folded dataset name ➜ concept
    for ds in sm.datasets:
        concept_of[_fold(ds.name)] = c = unique(pascal(ds.name), taken)
        taken.add(c)

    fk_fields: dict[str, set[str]] = {}  # folded dataset ➜ folded fields used as foreign keys
    for r in sm.relationships or []:
        fk_fields.setdefault(_fold(r.from_), set()).update(_fold(c) for c in r.from_columns)

    concepts: list[dict] = []
    generated: dict[str, dict] = {}
    attributes: dict[str, dict[str, str]] = {}  # concept ➜ field ➜ role concept
    identify: dict[str, list[str]] = {}  # concept ➜ primary key fields
    link_names: dict[str, str] = {}  # Ossie relationship name ➜ ontology relationship name
    for ds in sm.datasets:
        c = concept_of[_fold(ds.name)]
        path = f"datasets.{ds.name}"
        columns = {
            _fold(f.name): f.name
            for f in ds.fields or []
            if not f.expression.dialects and parse_identifier(f.expression.sql)
        }
        pk = [columns.get(_fold(k)) for k in ds.primary_key or []]
        if ds.primary_key and None in pk:
            report.add(
                Loss.APPROXIMATED,
                "derived_no_identifier",
                f"{path}.primary_key",
                "primary key uses derived fields; the node gets no key and no mapping",
            )
            pk = []
        pk = [p for p in pk if p]
        identify[c] = pk
        if not ds.primary_key:
            report.add(
                Loss.APPROXIMATED,
                "derived_no_key",
                path,
                "dataset has no primary key: its node gets no KEY constraint and no mapping",
            )

        attrs: dict[str, str] = {}
        rels: list[dict] = []
        for f in ds.fields or []:
            folded = _fold(f.name)
            if folded not in columns:
                report.add(
                    Loss.APPROXIMATED,
                    "derived_field_not_property",
                    f"{path}.fields.{f.name}",
                    "derived (expression) field doesn't become a node property",
                )
                continue
            if folded in fk_fields.get(_fold(ds.name), set()) and f.name not in pk:
                continue  # replaced by the relationship
            role = _ROLE_FOR_DATATYPE.get(f.datatype.value if f.datatype else "", "String")
            if role in ALL_GENERATED:
                generated.setdefault(role, ALL_GENERATED[role])
            attrs[f.name] = role
            rel: dict[str, Any] = {"name": f.name}
            if f.description:
                rel["description"] = f.description
            rel["roles"] = [{"concept": role}]
            rel["multiplicity"] = "OneToOne" if pk == [f.name] else "ManyToOne"
            rel["verbalizes"] = [f"{{{c}}} has {phrase(f.name)} {{{role}}}"]
            rels.append(rel)
        attributes[c] = attrs
        untyped = [f.name for f in ds.fields or [] if f.datatype is None and f.name in attrs]
        if untyped:
            report.add(
                Loss.APPROXIMATED,
                "derived_untyped_fields",
                path,
                f"{len(untyped)} field(s) without a datatype become STRING properties",
            )

        names = set(attrs)
        for r in sm.relationships or []:
            if _fold(r.from_) != _fold(ds.name) or _fold(r.to) not in concept_of:
                continue
            link_names[r.name] = name = unique(r.name, names)
            names.add(name)
            tgt = concept_of[_fold(r.to)]
            role = {"concept": tgt, "name": "target"} if tgt == c else {"concept": tgt}
            tname = ":target" if tgt == c else ""
            rels.append(
                {
                    "name": name,
                    "roles": [role],
                    "multiplicity": "ManyToOne",
                    "verbalizes": [f"{{{c}}} {phrase(name)} {{{tgt}{tname}}}"],
                }
            )

        concept: dict[str, Any] = {"concept": c, "type": "EntityType"}
        if ds.description:
            concept["description"] = ds.description
        if pk:
            concept["identify_by"] = pk
        if rels:
            concept["relationships"] = rels
        concepts.append(concept)

    ontology = {
        "version": SPEC_VERSION,
        "name": sm.name,
        "ontology": list(generated.values()) + concepts,
    }
    if sm.description:
        ontology["description"] = sm.description
    mapping = _mapping(sm, concept_of, attributes, identify, link_names, report)
    return ontology, mapping


def _mapping(sm, concept_of, attributes, identify, link_names, report: LossReport) -> dict:
    cms: dict[str, dict] = {}
    datasets = {_fold(d.name): d for d in sm.datasets}

    def ident(ds_name: str, fields: list[str]) -> dict:
        return {
            "referent_mappings": [
                {"relationship": k, "expression": column_ref(ds_name, f)} for k, f in fields
            ]
        }

    for ds in sm.datasets:
        c = concept_of[_fold(ds.name)]
        pk = identify[c]
        if not pk:
            continue
        identity = ident(ds.name, [(k, k) for k in pk])
        cm = cms.setdefault(c, {"concept": c})
        cm["object_mappings"] = [identity]
        children = [
            {
                "object_mapping": {"concept": role, "expression": column_ref(ds.name, f)},
                "relationship": f,
            }
            for f, role in attributes[c].items()
            if f not in pk
        ]
        if children:
            cm["link_mappings"] = [{"object_mapping": identity, "children": children}]

    for r in sm.relationships or []:
        src_ds = datasets.get(_fold(r.from_))
        tgt_ds = datasets.get(_fold(r.to))
        if src_ds is None or tgt_ds is None:
            continue
        c, tgt = concept_of[_fold(r.from_)], concept_of[_fold(r.to)]
        to_cols = [_fold(x) for x in r.to_columns]
        target_pk = identify[tgt]
        if not identify[c] or not target_pk or sorted(to_cols) != sorted(map(_fold, target_pk)):
            report.add(
                Loss.APPROXIMATED,
                "derived_relationship_not_mapped",
                f"relationships.{r.name}",
                "a relationship is mapped only when both datasets have primary keys and it "
                "references the target's primary key",
            )
            continue
        target_ident = ident(
            src_ds.name, [(k, r.from_columns[to_cols.index(_fold(k))]) for k in target_pk]
        )
        root = {
            "object_mapping": ident(src_ds.name, [(k, k) for k in identify[c]]),
            "children": [
                {
                    "object_mapping": {"concept": tgt, **target_ident},
                    "relationship": link_names.get(r.name, r.name),
                }
            ],
        }
        cms.setdefault(c, {"concept": c}).setdefault("link_mappings", []).append(root)

    return {
        "version": SPEC_VERSION,
        "name": f"{snake(sm.name)}_mapping",
        "ontology_ref": {"name": sm.name},
        "semantic_model_ref": {"name": sm.name},
        "concept_mappings": list(cms.values()),
    }
