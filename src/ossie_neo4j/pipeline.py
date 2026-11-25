"""The first stages of the conversion pipeline: parse ➜ validate ➜ normalize.

Phase 1 adds the remaining stages: map through the IR, emit, re-validate, and the loss report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .io import DocumentKind, detect_kind, load
from .issues import Issue, errors, join_path
from .ogs import models as ogs_models
from .ogs.normalize import normalize as ogs_normalize
from .ogs.rules import check_rules as ogs_rules
from .ossie import models as ossie_models
from .ossie.normalize import normalize as ossie_normalize
from .ossie.rules import check_ontology, check_semantic_model
from .schemas import validate_schema

_MODEL_FOR: dict[DocumentKind, type[BaseModel]] = {
    DocumentKind.OGS: ogs_models.GraphModel,
    DocumentKind.OSSIE_SEMANTIC_MODEL: ossie_models.SemanticModel,
    DocumentKind.OSSIE_ONTOLOGY: ossie_models.Ontology,
    DocumentKind.OSSIE_MAPPING: ossie_models.MappingDocument,
}


@dataclass
class Loaded:
    kind: DocumentKind
    raw: dict[str, Any]
    model: BaseModel | None = None
    normalized: BaseModel | None = None
    issues: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not errors(self.issues)


def read(source: str | Path | dict[str, Any], kind: DocumentKind | None = None) -> Loaded:
    """Parse, validate and normalize one document.

    The stages run in order: JSON Schema, then the Pydantic model, then the cross-reference
    rules, then normalization. A stage runs only if the previous one found no errors, so one
    mistake doesn't cascade into many issues (the OGS libraries do the same).
    """
    raw = source if isinstance(source, dict) else load(source)
    kind = kind or detect_kind(raw)
    result = Loaded(kind=kind, raw=raw)

    result.issues += validate_schema(raw, kind)
    if not result.ok:
        return result

    try:
        result.model = _MODEL_FOR[kind].model_validate(raw)
    except ValidationError as e:  # should be rare once the schema passed; kept as a safety net
        for err in e.errors():
            result.issues.append(Issue("model_violation", err["msg"], join_path(*err["loc"])))
        return result

    result.issues += _rules(kind, result.model)
    if not result.ok:
        return result

    if kind is DocumentKind.OGS:
        result.normalized, more = ogs_normalize(result.model)
    else:
        result.normalized, more = ossie_normalize(result.model)
    result.issues += more
    return result


def _rules(kind: DocumentKind, model: BaseModel) -> list[Issue]:
    if kind is DocumentKind.OGS:
        return ogs_rules(model)
    if kind is DocumentKind.OSSIE_SEMANTIC_MODEL:
        return check_semantic_model(model)
    if kind is DocumentKind.OSSIE_ONTOLOGY:
        issues = check_ontology(model)
        for i, m in enumerate(model.ontology_mappings or []):
            issues += [
                Issue(
                    x.code,
                    x.message,
                    join_path("ontology_mappings", i, "semantic_model", x.path),
                    x.severity,
                )
                for x in check_semantic_model(m.semantic_model)
            ]
        return issues
    # Mapping documents: references are checked in phase 3, once all three documents are present.
    return []
