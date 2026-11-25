"""Reading and writing YAML/JSON documents, and telling the document kinds apart."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml


class DocumentKind(StrEnum):
    OGS = "ogs"
    OSSIE_SEMANTIC_MODEL = "ossie-semantic-model"
    OSSIE_ONTOLOGY = "ossie-ontology"
    OSSIE_MAPPING = "ossie-mapping"


class UnknownDocumentKind(ValueError):
    pass


def load(path: str | Path) -> dict[str, Any]:
    """Load a YAML or JSON file into a plain dict.

    JSON is a subset of YAML, but ``.json`` files are parsed with :mod:`json` so that the error
    messages are clearer.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{p}: top level must be a mapping, got {type(data).__name__}")
    return data


def dump(data: dict[str, Any], path: str | Path) -> None:
    p = Path(path)
    if p.suffix.lower() == ".json":
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    else:
        p.write_text(dumps_yaml(data), encoding="utf-8")


def dumps_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def detect_kind(doc: dict[str, Any]) -> DocumentKind:
    """Work out the kind of a document from its root keys.

    Neither spec declares the kind explicitly. These root keys are each required by exactly one
    of the four schemas:

    - OGS: ``schema`` and ``id``
    - Ossie semantic model: ``datasets``
    - Ossie ontology: ``ontology``
    - Ossie mapping: ``concept_mappings`` with ``ontology_ref``
    """
    if "schema" in doc and "id" in doc:
        return DocumentKind.OGS
    if "ontology" in doc:
        return DocumentKind.OSSIE_ONTOLOGY
    if "concept_mappings" in doc and "ontology_ref" in doc:
        return DocumentKind.OSSIE_MAPPING
    if "datasets" in doc:
        return DocumentKind.OSSIE_SEMANTIC_MODEL
    raise UnknownDocumentKind(
        "cannot tell document kind: expected OGS (schema + id), Ossie ontology (ontology), "
        "Ossie mapping (concept_mappings + ontology_ref) or Ossie semantic model (datasets)"
    )
