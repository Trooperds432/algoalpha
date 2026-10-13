"""Commission, slippage and liquidity assumptions for bar execution."""

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Protocol, runtime_checkable

from quant_terminal.core.exceptions import DomainError


class SlippageKind(StrEnum):
    NONE = "none"
    FIXED = "fixed"
    BASIS_POINTS = "basis_point"
    VOLUME_SHARE = "volume_share"


def _nonnegative(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise DomainError(f"{field} must be a nonnegative finite Decimal")
    return value


def _context() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN)


@runtime_checkable
class CommissionModel(Protocol):
    """Replaceable cumulative commission model contract."""

    def incremental_fee(
        self,
        cumulative_quantity: Decimal,
        fill_quantity: Decimal,
        already_charged: Decimal,
    ) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class PerUnitCommission:
    """Per-unit commission with optional cumulative per-order minimum."""

    per_unit_rate: Decimal = Decimal(0)
    minimum: Decimal | None = None

    def __post_init__(self) -> None:
        _nonnegative(self.per_unit_rate, "per_unit_rate")
        if self.minimum is not None:
            _nonnegative(self.minimum, "minimum")

    def incremental_fee(
        self,
        cumulative_quantity: Decimal,
        fill_quantity: Decimal,
        already_charged: Decimal,
    ) -> Decimal:
        with localcontext(_context()):
            total_quantity = cumulative_quantity + fill_quantity
            target = self.per_unit_rate * total_quantity
            if self.minimum is not None:
                target = max(target, self.minimum)
            return target - already_charged


@runtime_checkable
class SlippageModel(Protocol):
    """Replaceable slippage model contract."""

    def amount(self, reference: Decimal, participation: Decimal) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class ConfiguredSlippage:
    kind: SlippageKind = SlippageKind.NONE
    value: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SlippageKind):
            raise DomainError("kind must be a supported SlippageKind")
        _nonnegative(self.value, "slippage value")
        if self.kind is SlippageKind.BASIS_POINTS and self.value > Decimal("10000"):
            raise DomainError("basis-point slippage may not exceed 10000")
        if self.kind is SlippageKind.VOLUME_SHARE and self.value > Decimal(1):
            raise DomainError("volume-share coefficient must be at most 1")
        if self.kind is SlippageKind.NONE and self.value != 0:
            raise DomainError("no-slippage model requires value zero")

    def amount(self, reference: Decimal, participation: Decimal) -> Decimal:
        with localcontext(_context()):
            if self.kind is SlippageKind.NONE:
                return Decimal(0)
            if self.kind is SlippageKind.FIXED:
                return self.value
            if self.kind is SlippageKind.BASIS_POINTS:
                return reference * self.value / Decimal(10000)
            return reference * self.value * participation * participation


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    maximum_participation: Decimal = Decimal(1)
    commission: CommissionModel = PerUnitCommission()
    slippage: SlippageModel = ConfiguredSlippage()
    zero_cost_assumption: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.maximum_participation, Decimal)
            or not self.maximum_participation.is_finite()
            or not Decimal(0) < self.maximum_participation <= Decimal(1)
        ):
            raise DomainError("maximum_participation must be greater than 0 and at most 1")
        if not isinstance(self.commission, CommissionModel) or not callable(
            getattr(self.commission, "incremental_fee", None)
        ):
            raise DomainError("commission model must implement incremental_fee")
        if not isinstance(self.slippage, SlippageModel) or not callable(
            getattr(self.slippage, "amount", None)
        ):
            raise DomainError("slippage model must implement amount")
        zero_commission = (
            isinstance(self.commission, PerUnitCommission)
            and self.commission.per_unit_rate == 0
            and self.commission.minimum in (None, Decimal(0))
        )
        zero_slippage = (
            isinstance(self.slippage, ConfiguredSlippage)
            and self.slippage.kind is SlippageKind.NONE
            and self.slippage.value == 0
        )
        if zero_commission and zero_slippage:
            if self.zero_cost_assumption not in (None, "ZERO_COST_ASSUMPTION"):
                raise DomainError("Zero-cost label must be ZERO_COST_ASSUMPTION")
        elif self.zero_cost_assumption is not None:
            raise DomainError("ZERO_COST_ASSUMPTION label requires zero cost models")
