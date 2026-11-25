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
from ..pipeline import read
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
    output: dict[str, Any]
    kind: DocumentKind
    report: LossReport
    input_issues: list[Issue] = field(default_factory=list)
    """Warnings found in the input. Inputs with errors raise :class:`ConversionError`."""


Source = str | Path | dict[str, Any]


def to_ossie(source: Source, *, stash: bool = True) -> Conversion:
    """Convert an OGS document to an Ossie ontology document."""
    loaded = _read(source, DocumentKind.OGS)
    out, report = ogs_to_ossie(loaded.normalized, stash=stash)
    _check_output(out, DocumentKind.OSSIE_ONTOLOGY)
    return Conversion(out, DocumentKind.OSSIE_ONTOLOGY, report, loaded.issues)


def to_neo4j(source: Source, *, stash: bool = True) -> Conversion:
    """Convert an Ossie ontology document to an OGS document."""
    loaded = _read(source, DocumentKind.OSSIE_ONTOLOGY)
    out, report = ossie_to_ogs(loaded.normalized, stash=stash)
    _check_output(out, DocumentKind.OGS)
    return Conversion(out, DocumentKind.OGS, report, loaded.issues)


def _read(source: Source, expected: DocumentKind):
    loaded = read(source)
    if loaded.kind is not expected:
        raise ConversionError(f"expected {expected} input, got {loaded.kind}", [])
    if not loaded.ok:
        raise ConversionError(f"input is not a valid {expected} document", loaded.issues)
    return loaded


def _check_output(out: dict[str, Any], kind: DocumentKind) -> None:
    """The converter must always produce valid output. If it doesn't, that is a bug here."""
    checked = read(out, kind)
    errors = [i for i in checked.issues if i.severity is Severity.ERROR]
    if errors:
        raise ConversionError(f"internal error: converter produced an invalid {kind}", errors)
