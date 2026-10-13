"""Distinct immutable identifiers with reproducible and random construction."""

from dataclasses import dataclass
from typing import Self
from uuid import uuid4

from .exceptions import DomainError


@dataclass(frozen=True, slots=True)
class Identifier:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value or len(self.value) > 128:
            raise DomainError("Identifier must be a nonempty string of at most128 characters")
        if any(not (c.isascii() and (c.isalnum() or c in "-_:.")) for c in self.value):
            raise DomainError("Identifier contains an unsupported character")

    @classmethod
    def new(cls) -> Self:
        return cls(str(uuid4()))


@dataclass(frozen=True, slots=True)
class RunId(Identifier):
    """Experiment identity."""


@dataclass(frozen=True, slots=True)
class StrategyId(Identifier):
    """Registered strategy identity."""


@dataclass(frozen=True, slots=True)
class OrderId(Identifier):
    """Order identity."""


@dataclass(frozen=True, slots=True)
class FillId(Identifier):
    """Fill identity."""


@dataclass(frozen=True, slots=True)
class InstrumentId(Identifier):
    """Stable instrument identity, independent of ticker reuse."""
