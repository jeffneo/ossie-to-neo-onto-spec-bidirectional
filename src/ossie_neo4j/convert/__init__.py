"""Conversion between OGS and Ossie: the public API.

    result = to_ossie("movies.yaml")      # OGS ➜ Ossie ontology
    result = to_neo4j("x.ontology.yaml")  # Ossie ontology ➜ OGS

Both calls validate and normalize the input, convert it, re-validate the output against the
target spec, and return the output together with a :class:`LossReport`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..io import DocumentKind
from ..issues import Issue, Severity
from ..ossie.models import MappingDocument, Ontology
from ..ossie.normalize import normalize as ossie_normalize
from ..pipeline import read
from .derive import derive_ontology_and_mapping
from .report import Loss, LossEntry, LossReport
from .to_ogs import ossie_to_ogs
from .to_ossie import ogs_to_ossie

__all__ = [
    "ConversionError",
    "Conversion",
    "Loss",
    "LossEntry",
    "LossReport",
    "to_neo4j",
    "to_ossie",
]


class ConversionError(Exception):
    def __init__(self, message: str, issues: list[Issue]):
        super().__init__(message + "".join(f"\n  {i}" for i in issues))
        self.issues = issues


@dataclass
class Conversion:
    documents: dict[DocumentKind, dict[str, Any]]
    """Every output document by kind. OGS ➜ Ossie may give an ontology, a semantic model, or
    both. Ossie ➜ OGS always gives a single OGS document."""
    report: LossReport
    input_issues: list[Issue] = field(default_factory=list)
    """Warnings found in the inputs. Inputs with errors raise :class:`ConversionError`."""

    @property
    def output(self) -> dict[str, Any]:
        """The main output document: the OGS document, or else the ontology, or else the
        semantic model."""
        for kind in (DocumentKind.OGS, DocumentKind.OSSIE_ONTOLOGY):
            if kind in self.documents:
                return self.documents[kind]
        return self.documents[DocumentKind.OSSIE_SEMANTIC_MODEL]

    @property
    def kind(self) -> DocumentKind:
        return next(k for k, v in self.documents.items() if v is self.output)


Source = str | Path | dict[str, Any]


def to_ossie(source: Source, *, stash: bool = True) -> Conversion:
    """Convert an OGS document to Ossie documents.

    The result holds:
    - an ontology if the model has nodes;
    - a semantic model if it has ``data_source_tables``;
    - a mapping document if it has nodes, tables and ``data_source_mappings``.
    """
    loaded = _read(source, {DocumentKind.OGS})
    onto, sm, mapping, report = ogs_to_ossie(loaded.normalized, stash=stash)
    documents: dict[DocumentKind, dict[str, Any]] = {}
    if onto is not None:
        documents[DocumentKind.OSSIE_ONTOLOGY] = onto
    if sm is not None:
        documents[DocumentKind.OSSIE_SEMANTIC_MODEL] = sm
    if mapping is not None:
        documents[DocumentKind.OSSIE_MAPPING] = mapping
    if not documents:
        raise ConversionError("nothing to convert: the model has no nodes and no tables", [])
    for kind, doc in documents.items():
        _check_output(doc, kind)
    return Conversion(documents, report, loaded.issues)


def to_neo4j(
    source: Source | None = None,
    *,
    semantic_model: Source | None = None,
    mapping: Source | None = None,
    stash: bool = True,
    derive_graph: bool = False,
) -> Conversion:
    """Convert Ossie documents to one OGS document.

    ``source`` may be an ontology or a semantic model. To combine an ontology with its semantic
    model, pass ``semantic_model=`` as well. To add the mapping document that links the two,
    also pass ``mapping=``.

    ``derive_graph=True`` is core-only mode, for a semantic model given on its own. It derives
    nodes, relationships and mappings from the datasets (see ``convert/derive.py``).
    """
    onto = sm = None
    issues: list[Issue] = []
    if source is not None:
        loaded = _read(source, {DocumentKind.OSSIE_ONTOLOGY, DocumentKind.OSSIE_SEMANTIC_MODEL})
        issues += loaded.issues
        if loaded.kind is DocumentKind.OSSIE_ONTOLOGY:
            onto = loaded.normalized
        else:
            sm = loaded.normalized
    if semantic_model is not None:
        if sm is not None:
            raise ConversionError("two semantic models given", [])
        loaded = _read(semantic_model, {DocumentKind.OSSIE_SEMANTIC_MODEL})
        issues += loaded.issues
        sm = loaded.normalized
    if onto is None and sm is None:
        raise ConversionError("need an Ossie ontology, a semantic model, or both", [])
    md = None
    if mapping is not None:
        if onto is None or sm is None:
            raise ConversionError("a mapping needs both its ontology and its semantic model", [])
        loaded = _read(mapping, {DocumentKind.OSSIE_MAPPING})
        issues += loaded.issues
        md = loaded.normalized
        for ref, doc, what in (
            (md.ontology_ref, onto, "ontology"),
            (md.semantic_model_ref, sm, "semantic model"),
        ):
            if ref.name != doc.name:
                issues.append(
                    Issue(
                        "mapping_ref_mismatch",
                        f"mapping refers to {what} {ref.name!r}, but got {doc.name!r}",
                        f"{what.replace(' ', '_')}_ref.name",
                        Severity.WARNING,
                    )
                )
    if derive_graph:
        if onto is not None or md is not None or sm is None:
            raise ConversionError("derive_graph needs a semantic model on its own", [])
        report = LossReport("ossie->ogs")
        onto_d, map_d = derive_ontology_and_mapping(sm, report)
        onto = ossie_normalize(Ontology.model_validate(onto_d))[0]
        out, report = ossie_to_ogs(
            onto,
            sm,
            MappingDocument.model_validate(map_d),
            stash=stash,
            report=report,
            keep_mapping_document=False,
        )
    else:
        out, report = ossie_to_ogs(onto, sm, md, stash=stash)
    _check_output(out, DocumentKind.OGS)
    return Conversion({DocumentKind.OGS: out}, report, issues)


def _read(source: Source, expected: set[DocumentKind]):
    loaded = read(source)
    if loaded.kind not in expected:
        names = " or ".join(sorted(expected))
        raise ConversionError(f"expected {names} input, got {loaded.kind}", [])
    if not loaded.ok:
        raise ConversionError(f"input is not a valid {loaded.kind} document", loaded.issues)
    return loaded


def _check_output(out: dict[str, Any], kind: DocumentKind) -> None:
    """The converter must always produce valid output. If it doesn't, that is a bug here."""
    checked = read(out, kind)
    errors = [i for i in checked.issues if i.severity is Severity.ERROR]
    if errors:
        raise ConversionError(f"internal error: converter produced an invalid {kind}", errors)
