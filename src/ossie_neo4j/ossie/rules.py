"""The Ossie cross-reference rules that JSON Schema cannot express.

Sources:
- semantic model: ``core-spec/spec.md`` (Datasets, Relationships, Name Resolution). Names resolve
  case-insensitively.
- ontology: ``ontology/ontology.md`` (built-in concepts, extends, roles, multiplicities,
  identify_by). The ontology spec does not say whether its names are case-insensitive, so
  ontology references are matched exactly.

Checks that need the expression grammar (metric SQL, ``requires`` and ``derived_by``) are out of
scope for now. So are mapping-document references, which need all three documents (phase 3).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ..issues import Issue, Severity, join_path
from .models import (
    BUILTIN_CONCEPTS,
    ConceptType,
    Dataset,
    Multiplicity,
    Ontology,
    SemanticModel,
)

Add = Callable[[Issue], None]


def fold(name: str) -> str:
    """The case-insensitive comparison key used for Ossie core names."""
    return name.casefold()


# --- semantic model --------------------------------------------------------------------------


def check_semantic_model(model: SemanticModel) -> list[Issue]:
    issues: list[Issue] = []
    add = issues.append

    datasets: dict[str, Dataset] = {}
    for ds in model.datasets:
        if fold(ds.name) in datasets:
            add(
                Issue(
                    "duplicate_dataset_name",
                    f"dataset {ds.name!r} is declared twice",
                    join_path("datasets", ds.name),
                )
            )
        datasets.setdefault(fold(ds.name), ds)
        _check_dataset(add, ds)

    _duplicates(
        add,
        (r.name for r in model.relationships or []),
        "relationships",
        "duplicate_relationship_name",
    )
    _duplicates(add, (m.name for m in model.metrics or []), "metrics", "duplicate_metric_name")

    for rel in model.relationships or []:
        path = join_path("relationships", rel.name)
        ends = {"from": rel.from_, "to": rel.to}
        resolved = {}
        for end, ds_name in ends.items():
            ds = datasets.get(fold(ds_name))
            if ds is None:
                add(
                    Issue(
                        "unknown_relationship_dataset",
                        f"no dataset named {ds_name!r}",
                        join_path(path, end),
                    )
                )
            resolved[end] = ds
        if len(rel.from_columns) != len(rel.to_columns):
            add(
                Issue(
                    "relationship_column_count_mismatch",
                    f"from_columns has {len(rel.from_columns)} entries, "
                    f"to_columns {len(rel.to_columns)}",
                    path,
                )
            )
        for end, cols in (("from", rel.from_columns), ("to", rel.to_columns)):
            ds = resolved[end]
            if ds is None:
                continue
            known = {fold(f.name) for f in ds.fields or []}
            for c in cols:
                if fold(c) not in known:
                    add(
                        Issue(
                            "unknown_relationship_field",
                            f"dataset {ds.name!r} has no field {c!r}",
                            join_path(path, f"{end}_columns"),
                        )
                    )
    return issues


def _check_dataset(add: Add, ds: Dataset) -> None:
    path = join_path("datasets", ds.name)
    names = [f.name for f in ds.fields or []] + [m.name for m in ds.metrics or []]
    _duplicates(add, names, path, "duplicate_field_name", what="field or metric")
    fields = {fold(f.name) for f in ds.fields or []}
    keys = ([("primary_key", ds.primary_key)] if ds.primary_key else []) + [
        (f"unique_keys.{i}", k) for i, k in enumerate(ds.unique_keys or [])
    ]
    for where, key in keys:
        seen: set[str] = set()
        for f in key:
            if fold(f) not in fields:
                add(Issue("unknown_key_field", f"no field named {f!r}", join_path(path, where)))
            if fold(f) in seen:
                add(
                    Issue(
                        "duplicate_key_field",
                        f"{f!r} appears twice (names are case-insensitive)",
                        join_path(path, where),
                    )
                )
            seen.add(fold(f))


def _duplicates(add: Add, names: Iterable[str], path: str, code: str, what: str = "name") -> None:
    seen: set[str] = set()
    for n in names:
        if fold(n) in seen:
            add(
                Issue(
                    code,
                    f"{what} {n!r} is declared twice (names are case-insensitive)",
                    join_path(path, n),
                )
            )
        seen.add(fold(n))


# --- ontology --------------------------------------------------------------------------------


def check_ontology(onto: Ontology) -> list[Issue]:
    issues: list[Issue] = []
    add = issues.append

    concepts = {}
    for c in onto.ontology:
        path = join_path("ontology", c.concept)
        if c.concept in BUILTIN_CONCEPTS:
            add(Issue("redefines_builtin_concept", f"{c.concept!r} is a built-in concept", path))
        elif c.concept in concepts:
            add(Issue("duplicate_concept", f"concept {c.concept!r} is declared twice", path))
        concepts.setdefault(c.concept, c)

    def type_of(name: str) -> ConceptType | None:
        if name in BUILTIN_CONCEPTS:
            return BUILTIN_CONCEPTS[name]
        return concepts[name].type if name in concepts else None

    for c in onto.ontology:
        path = join_path("ontology", c.concept)
        for parent in c.extends or []:
            ptype = type_of(parent)
            if ptype is None:
                add(
                    Issue(
                        "unknown_concept",
                        f"extends unknown concept {parent!r}",
                        join_path(path, "extends"),
                    )
                )
            elif ptype != c.type:
                add(
                    Issue(
                        "invalid_extends",
                        f"{c.type} {c.concept!r} cannot extend {ptype} {parent!r}",
                        join_path(path, "extends"),
                    )
                )

        rel_names: set[str] = set()
        for rel in c.relationships or []:
            rpath = join_path(path, "relationships", rel.name)
            if rel.name in rel_names:
                add(
                    Issue(
                        "duplicate_relationship", f"{c.concept}.{rel.name} is declared twice", rpath
                    )
                )
            rel_names.add(rel.name)
            for role in rel.roles or []:
                if type_of(role.concept) is None:
                    add(
                        Issue(
                            "unknown_concept",
                            f"role refers to unknown concept {role.concept!r}",
                            join_path(rpath, "roles"),
                        )
                    )
            if rel.multiplicity == Multiplicity.ONE_TO_ONE and rel.arity != 2:
                add(
                    Issue(
                        "one_to_one_not_binary",
                        "OneToOne is only allowed on binary relationships; "
                        f"this one has {rel.arity} roles",
                        join_path(rpath, "multiplicity"),
                    )
                )

        # identify_by may name a relationship inherited from a supertype. For example, in the
        # upstream sap_business_partner example, Customer extends BusinessPartner and is
        # identified by BusinessPartner.nr.
        available = rel_names | _inherited_relationship_names(c.concept, concepts)
        for ident in c.identify_by or []:
            if ident not in available:
                add(
                    Issue(
                        "unknown_identifying_relationship",
                        f"identify_by names {ident!r}, which is not a relationship of "
                        f"{c.concept!r} or its supertypes",
                        join_path(path, "identify_by"),
                    )
                )

    _check_extends_graph(add, concepts)
    return issues


def _inherited_relationship_names(name: str, concepts: dict) -> set[str]:
    """Relationship names declared on any transitive supertype of ``name`` (cycle-safe)."""
    found: set[str] = set()
    seen = {name}
    todo = list(concepts[name].extends or []) if name in concepts else []
    while todo:
        parent = todo.pop()
        if parent in seen or parent not in concepts:
            continue
        seen.add(parent)
        found |= {r.name for r in concepts[parent].relationships or []}
        todo += concepts[parent].extends or []
    return found


def _check_extends_graph(add: Add, concepts: dict) -> None:
    """Report ``extends`` cycles, and value types that never reach a built-in value type."""
    builtin_values = {n for n, t in BUILTIN_CONCEPTS.items() if t == ConceptType.VALUE_TYPE}

    def reaches_builtin(name: str, stack: tuple[str, ...]) -> bool | None:
        if name in builtin_values:
            return True
        if name in stack:
            return None  # cycle
        c = concepts.get(name)
        if c is None:
            return False
        results = [reaches_builtin(p, (*stack, name)) for p in c.extends or []]
        if None in results:
            return None
        return any(results)

    for name, c in concepts.items():
        if c.type != ConceptType.VALUE_TYPE:
            continue
        r = reaches_builtin(name, ())
        path = join_path("ontology", name, "extends")
        if r is None:
            add(Issue("extends_cycle", f"value type {name!r} is part of an extends cycle", path))
        elif not r:
            add(
                Issue(
                    "value_type_without_builtin_base",
                    f"value type {name!r} must extend a built-in value type (directly or not)",
                    path,
                    Severity.ERROR,
                )
            )
