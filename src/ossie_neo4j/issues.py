"""Issues reported by validation, normalization and (later) conversion."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass(frozen=True)
class Issue:
    """One finding about a document.

    The fields follow the OGS libraries' issue shape: ``code``, ``message``, ``path`` and
    ``details``. ``code`` is a stable snake_case identifier. ``path`` is a dot-separated
    location such as ``relationships.acted-in.to.node``.
    """

    code: str
    message: str
    path: str = ""
    severity: Severity = Severity.ERROR
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        where = f" at {self.path}" if self.path else ""
        return f"[{self.severity}] {self.code}{where}: {self.message}"


def errors(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity is Severity.ERROR]


def join_path(*parts: object) -> str:
    return ".".join(str(p) for p in parts if p != "" and p is not None)
