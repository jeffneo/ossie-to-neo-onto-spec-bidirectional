"""The loss report: every construct that was not carried across natively."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum


class Loss(StrEnum):
    STASHED = "stashed"
    """There is no native home in the target. The value is kept in vendor annotations or custom
    data and restored on the way back."""

    APPROXIMATED = "approximated"
    """Mapped to the nearest native construct, so some meaning changes. For example, Decimal
    becomes FLOAT, and an n-ary relationship becomes an intermediate node."""

    DROPPED = "dropped"
    """Not carried at all. Happens only with ``stash=False``, or when the source is
    inconsistent."""


@dataclass(frozen=True)
class LossEntry:
    kind: Loss
    code: str
    path: str
    message: str


@dataclass
class LossReport:
    direction: str
    entries: list[LossEntry] = field(default_factory=list)

    def add(self, kind: Loss, code: str, path: str, message: str) -> None:
        self.entries.append(LossEntry(kind, code, path, message))

    def of(self, kind: Loss) -> list[LossEntry]:
        return [e for e in self.entries if e.kind is kind]

    @property
    def lossy(self) -> bool:
        """True if anything was approximated or dropped. Stashed values count as preserved."""
        return any(e.kind is not Loss.STASHED for e in self.entries)

    def codes(self) -> list[str]:
        return [e.code for e in self.entries]

    def to_dict(self) -> dict:
        return {"direction": self.direction, "entries": [asdict(e) for e in self.entries]}
