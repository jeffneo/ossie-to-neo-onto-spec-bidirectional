"""Offline JSON Schema validation against the pinned schemas of both specs."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from ..io import DocumentKind
from ..issues import Issue, join_path

OGS_SCHEMA_VERSION = "1.1.0"
OSSIE_SCHEMA_VERSION = "0.3.0.dev0"

# The Ossie schemas refer to each other through these URLs. We map them to the vendored files.
_OSSIE_RAW = "https://raw.githubusercontent.com/apache/ossie/main/"
_OSSIE_FILES = {
    "core-spec/ossie-schema.json": "ossie-schema.json",
    "ontology/ontology.json": "ontology.json",
    "ontology/mapping.json": "mapping.json",
}

_KIND_TO_FILE = {
    DocumentKind.OGS: (f"ogs-{OGS_SCHEMA_VERSION}", "schema.json"),
    DocumentKind.OSSIE_SEMANTIC_MODEL: (f"ossie-{OSSIE_SCHEMA_VERSION}", "ossie-schema.json"),
    DocumentKind.OSSIE_ONTOLOGY: (f"ossie-{OSSIE_SCHEMA_VERSION}", "ontology.json"),
    DocumentKind.OSSIE_MAPPING: (f"ossie-{OSSIE_SCHEMA_VERSION}", "mapping.json"),
}


def load_schema(kind: DocumentKind) -> dict[str, Any]:
    folder, name = _KIND_TO_FILE[kind]
    return _read(folder, name)


@cache
def _read(folder: str, name: str) -> dict[str, Any]:
    text = resources.files(__package__).joinpath(folder, name).read_text(encoding="utf-8")
    return json.loads(text)


@cache
def _registry() -> Registry:
    folder = f"ossie-{OSSIE_SCHEMA_VERSION}"
    pairs = []
    for upstream_path, local in _OSSIE_FILES.items():
        resource = Resource.from_contents(_read(folder, local))
        pairs.append((_OSSIE_RAW + upstream_path, resource))
        # Also register each schema under its own $id, so that relative refs resolve.
        pairs.append((resource.contents["$id"], resource))
    return Registry().with_resources(pairs)


@cache
def _validator(kind: DocumentKind) -> Draft202012Validator:
    schema = load_schema(kind)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, registry=_registry())


def validate_schema(doc: dict[str, Any], kind: DocumentKind) -> list[Issue]:
    """Validate ``doc`` against the pinned JSON Schema for ``kind``.

    Returns one issue per schema error, sorted by path. This covers only what JSON Schema can
    express. Cross-reference rules live in ``ossie_neo4j.ogs.rules`` and
    ``ossie_neo4j.ossie.rules``.
    """
    issues = []
    for err in _validator(kind).iter_errors(doc):
        issues.append(
            Issue(
                code="schema_violation",
                message=err.message,
                path=join_path(*err.absolute_path),
                details={"validator": err.validator, "schema_path": list(err.absolute_schema_path)},
            )
        )
    return sorted(issues, key=lambda i: i.path)
