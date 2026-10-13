"""Trusted in-process strategy interface and deterministic examples."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Context, Decimal, localcontext
from hashlib import sha256
from typing import Protocol

from quant_terminal.accounting.ledger import PortfolioSnapshot
from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId, OrderId
from quant_terminal.data.feed import DataFeed
from quant_terminal.data.model import Bar, InstrumentMeta
from quant_terminal.orders.models import OrderIntent, OrderSnapshot, Side, TradeType


def _context() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN)


def _order_id(strategy: str, instrument_id: InstrumentId, timestamp: datetime) -> OrderId:
    stamp = utc(timestamp).strftime("%Y%m%dT%H%M%S%f")
    identity = f"{strategy}-{instrument_id.value}-{stamp}"
    safe = "".join(
        char if char.isascii() and (char.isalnum() or char in "-_:.") else "-" for char in identity
    )
    if len(safe) > 128:
        safe = f"{safe[:59]}-{sha256(safe.encode()).hexdigest()}"
    return OrderId(safe)


def _positive_quantity(value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise DomainError("strategy quantity must be a positive finite Decimal")


@dataclass(frozen=True, slots=True)
class StrategyContext:
    current_time: datetime
    portfolio: PortfolioSnapshot
    active_orders: tuple[OrderSnapshot, ...]
    _feeds: Mapping[InstrumentId, DataFeed]
    _instruments: Mapping[InstrumentId, InstrumentMeta]

    def __post_init__(self) -> None:
        object.__setattr__(self, "current_time", utc(self.current_time))

    def history(
        self, instrument_id: InstrumentId, count: int, *, end: datetime | None = None
    ) -> tuple[Bar, ...]:
        try:
            feed = self._feeds[instrument_id]
        except KeyError as exc:
            raise DomainError("Strategy requested an unconfigured instrument") from exc
        return feed.history(end=end or self.current_time, count=count)

    def instrument(self, instrument_id: InstrumentId) -> InstrumentMeta:
        try:
            return self._instruments[instrument_id]
        except KeyError as exc:
            raise DomainError("Strategy requested an unconfigured instrument") from exc

    def position(self, instrument_id: InstrumentId) -> Decimal:
        return next(
            (
                position.quantity
                for position in self.portfolio.positions
                if position.instrument_id == instrument_id
            ),
            Decimal(0),
        )

    def has_active_order(self, instrument_id: InstrumentId, side: Side | None = None) -> bool:
        return any(
            order.intent.instrument.instrument_id == instrument_id
            and (side is None or order.intent.side is side)
            for order in self.active_orders
        )


class Strategy(Protocol):
    name: str

    def on_bar(self, context: StrategyContext, bar: Bar) -> tuple[OrderIntent, ...]: ...


@dataclass(slots=True)
class BuyAndHold:
    instrument_id: InstrumentId
    quantity: Decimal
    name: str = "buy_and_hold"

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, InstrumentId):
            raise DomainError("instrument_id must be an InstrumentId")
        _positive_quantity(self.quantity)

    def on_bar(self, context: StrategyContext, bar: Bar) -> tuple[OrderIntent, ...]:
        if bar.instrument_id != self.instrument_id or context.position(self.instrument_id) > 0:
            return ()
        if context.has_active_order(self.instrument_id):
            return ()
        return (
            _market_intent(
                self.name,
                context.instrument(self.instrument_id),
                Side.BUY,
                self.quantity,
                context.current_time,
                bar,
            ),
        )


@dataclass(slots=True)
class MovingAverageCross:
    instrument: InstrumentMeta
    quantity: Decimal
    fast_period: int = 5
    slow_period: int = 20
    name: str = "moving_average_cross"

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentMeta):
            raise DomainError("instrument must be InstrumentMeta")
        _positive_quantity(self.quantity)
        if type(self.fast_period) is not int or type(self.slow_period) is not int:
            raise DomainError("Moving-average windows must be integers")
        if self.fast_period <= 0 or self.slow_period <= self.fast_period:
            raise DomainError("Require 0 < fast_period < slow_period")

    def on_bar(self, context: StrategyContext, bar: Bar) -> tuple[OrderIntent, ...]:
        instrument_id = self.instrument.instrument_id
        if bar.instrument_id != instrument_id or context.has_active_order(instrument_id):
            return ()
        history = context.history(instrument_id, self.slow_period)
        if len(history) < self.slow_period:
            return ()
        with localcontext(_context()):
            fast = sum((item.close for item in history[-self.fast_period :]), Decimal(0)) / Decimal(
                self.fast_period
            )
            slow = sum((item.close for item in history), Decimal(0)) / Decimal(self.slow_period)
        position = context.position(instrument_id)
        side = (
            Side.BUY
            if fast > slow and position <= 0
            else Side.SELL
            if fast < slow and position > 0
            else None
        )
        size = self.quantity if side is Side.BUY else position
        if side is None or size <= 0:
            return ()
        return (_market_intent(self.name, self.instrument, side, size, context.current_time, bar),)


@dataclass(slots=True)
class SimpleMomentum:
    instrument: InstrumentMeta
    quantity: Decimal
    lookback: int = 20
    name: str = "simple_momentum"

    def __post_init__(self) -> None:
        if not isinstance(self.instrument, InstrumentMeta):
            raise DomainError("instrument must be InstrumentMeta")
        _positive_quantity(self.quantity)
        if type(self.lookback) is not int or self.lookback <= 0:
            raise DomainError("lookback must be a positive integer")

    def on_bar(self, context: StrategyContext, bar: Bar) -> tuple[OrderIntent, ...]:
        instrument_id = self.instrument.instrument_id
        if bar.instrument_id != instrument_id or context.has_active_order(instrument_id):
            return ()
        history = context.history(instrument_id, self.lookback + 1)
        if len(history) < self.lookback + 1:
            return ()
        current_quantity = context.position(instrument_id)
        side = (
            Side.BUY
            if history[-1].close > history[0].close and current_quantity <= 0
            else Side.SELL
            if history[-1].close < history[0].close and current_quantity > 0
            else None
        )
        size = self.quantity if side is Side.BUY else current_quantity
        if side is None or size <= 0:
            return ()
        return (_market_intent(self.name, self.instrument, side, size, context.current_time, bar),)


def _market_intent(
    strategy_name: str,
    instrument: InstrumentMeta,
    side: Side,
    quantity: Decimal,
    timestamp: datetime,
    bar: Bar,
) -> OrderIntent:
    max_execution_price = None
    if side is Side.BUY:
        with localcontext(_context()):
            units = (bar.close * Decimal("1.25") / instrument.tick_size).to_integral_value(
                rounding=ROUND_CEILING
            )
            max_execution_price = units * instrument.tick_size
    return OrderIntent(
        _order_id(strategy_name, instrument.instrument_id, bar.end),
        instrument,
        side,
        TradeType.MARKET,
        quantity,
        timestamp,
        max_execution_price=max_execution_price,
    )
