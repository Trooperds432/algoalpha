"""Immutable order, fill and completed execution-trade records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from quant_terminal.accounting.ledger import FillSide, LedgerFill
from quant_terminal.core.clock import utc
from quant_terminal.core.enums import OrderStatus
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import FillId, OrderId
from quant_terminal.data.model import InstrumentMeta


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class TradeType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


def _positive(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise DomainError(f"{field} must be a positive finite Decimal")
    return value


def _multiple(value: Decimal, step: Decimal) -> bool:
    value_tuple, step_tuple = value.as_tuple(), step.as_tuple()
    if not isinstance(value_tuple.exponent, int) or not isinstance(step_tuple.exponent, int):
        return False
    exponent = min(value_tuple.exponent, step_tuple.exponent)
    value_units = int("".join(map(str, value_tuple.digits)) or "0") * 10 ** (
        value_tuple.exponent - exponent
    )
    step_units = int("".join(map(str, step_tuple.digits)) or "0") * 10 ** (
        step_tuple.exponent - exponent
    )
    return bool(step_units != 0 and value_units % step_units == 0)


@dataclass(frozen=True, slots=True)
class OrderIntent:
    order_id: OrderId
    instrument: InstrumentMeta
    side: Side
    order_type: TradeType
    quantity: Decimal
    created_at: datetime
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    expires_at: datetime | None = None
    max_execution_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, OrderId):
            raise DomainError("order_id must be an OrderId")
        if not isinstance(self.instrument, InstrumentMeta):
            raise DomainError("instrument metadata is required")
        if not isinstance(self.side, Side) or not isinstance(self.order_type, TradeType):
            raise DomainError("side and order_type must use their supported enums")
        quantity = _positive(self.quantity, "quantity")
        if not _multiple(quantity, self.instrument.quantity_step):
            raise DomainError("Order quantity violates instrument quantity step")
        created_at = utc(self.created_at)
        expires_at = utc(self.expires_at) if self.expires_at is not None else None
        if expires_at is not None and expires_at <= created_at:
            raise DomainError("expires_at must follow created_at")
        for field in ("limit_price", "stop_price"):
            value = getattr(self, field)
            if value is not None:
                _positive(value, field)
                if not _multiple(value, self.instrument.tick_size):
                    raise DomainError(f"{field} violates instrument tick size")
        if self.max_execution_price is not None:
            _positive(self.max_execution_price, "max_execution_price")
            if not _multiple(self.max_execution_price, self.instrument.tick_size):
                raise DomainError("max_execution_price violates instrument tick size")
            if self.side is not Side.BUY or self.order_type not in (
                TradeType.MARKET,
                TradeType.STOP,
            ):
                raise DomainError("max_execution_price is only supported for market/stop buys")
        needs_limit = self.order_type in (TradeType.LIMIT, TradeType.STOP_LIMIT)
        needs_stop = self.order_type in (TradeType.STOP, TradeType.STOP_LIMIT)
        if (self.limit_price is not None) != needs_limit:
            raise DomainError("limit_price must be set only for limit and stop-limit orders")
        if (self.stop_price is not None) != needs_stop:
            raise DomainError("stop_price must be set only for stop and stop-limit orders")
        if self.order_type is TradeType.STOP_LIMIT:
            assert self.limit_price is not None and self.stop_price is not None
            if self.side is Side.BUY and self.limit_price < self.stop_price:
                raise DomainError("Buy stop-limit price must be at or above stop price")
            if self.side is Side.SELL and self.limit_price > self.stop_price:
                raise DomainError("Sell stop-limit price must be at or below stop price")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: FillId
    order_id: OrderId
    instrument: InstrumentMeta
    side: Side
    quantity: Decimal
    reference_price: Decimal
    price: Decimal
    commission: Decimal
    timestamp: datetime
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.fill_id, FillId) or not isinstance(self.order_id, OrderId):
            raise DomainError("fill and order identifiers are required")
        if not isinstance(self.instrument, InstrumentMeta) or not isinstance(self.side, Side):
            raise DomainError("instrument and side are required")
        _positive(self.quantity, "fill quantity")
        _positive(self.reference_price, "fill reference price")
        _positive(self.price, "fill price")
        if (
            not isinstance(self.commission, Decimal)
            or not self.commission.is_finite()
            or self.commission < 0
        ):
            raise DomainError("commission must be a nonnegative finite Decimal")
        if not _multiple(self.quantity, self.instrument.quantity_step):
            raise DomainError("Fill quantity violates instrument quantity step")
        if not _multiple(self.price, self.instrument.tick_size):
            raise DomainError("Fill price violates instrument tick size")
        timestamp, observed_at = utc(self.timestamp), utc(self.observed_at)
        if observed_at < timestamp:
            raise DomainError("Fill cannot be observed before its economic timestamp")
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "observed_at", observed_at)


@dataclass(frozen=True, slots=True)
class OrderEvent:
    order_id: OrderId
    sequence: int
    status: OrderStatus
    timestamp: datetime
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class Trade:
    """Aggregated executions for one terminal order; not a round-trip trade."""

    order_id: OrderId
    side: Side
    quantity: Decimal
    average_price: Decimal
    commission: Decimal
    fill_ids: tuple[FillId, ...]


@dataclass(frozen=True, slots=True)
class OrderSnapshot:
    intent: OrderIntent
    status: OrderStatus
    accepted_at: datetime | None
    filled_quantity: Decimal
    remaining_quantity: Decimal
    events: tuple[OrderEvent, ...]
    fills: tuple[Fill, ...]
    reason: str | None
    trade: Trade | None


ExecutionTrade = Trade


def to_ledger_fill(fill: Fill) -> LedgerFill:
    """Convert an execution fact to the portfolio-ledger contract."""
    return LedgerFill(
        fill_id=fill.fill_id,
        instrument=fill.instrument,
        side=FillSide(fill.side.value),
        quantity=fill.quantity,
        price=fill.price,
        fee=fill.commission,
        timestamp=fill.timestamp,
    )
