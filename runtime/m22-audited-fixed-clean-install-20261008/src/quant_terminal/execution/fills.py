"""Pluggable order-to-bar matching policies."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.data.model import Bar
from quant_terminal.orders.models import OrderIntent, Side, TradeType


@dataclass(frozen=True, slots=True)
class FillDecision:
    """Result of applying a fill policy to one order and one observable bar."""

    triggered: bool
    trigger_ready_at: datetime | None
    reference_price: Decimal | None
    protected_limit: Decimal | None

    def __post_init__(self) -> None:
        if type(self.triggered) is not bool:
            raise DomainError("FillDecision.triggered must be boolean")
        if self.trigger_ready_at is not None:
            object.__setattr__(self, "trigger_ready_at", utc(self.trigger_ready_at))
        for field in ("reference_price", "protected_limit"):
            value = getattr(self, field)
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            ):
                raise DomainError(f"FillDecision.{field} must be positive and finite")


@runtime_checkable
class FillModel(Protocol):
    def match(
        self,
        intent: OrderIntent,
        bar: Bar,
        *,
        triggered: bool,
        trigger_ready_at: datetime | None,
    ) -> FillDecision: ...


class OHLCFillModel:
    """Documented deterministic matching rules for completed OHLC bars."""

    def match(
        self,
        intent: OrderIntent,
        bar: Bar,
        *,
        triggered: bool,
        trigger_ready_at: datetime | None,
    ) -> FillDecision:
        if intent.order_type is TradeType.MARKET:
            return FillDecision(triggered, trigger_ready_at, bar.open, None)
        if intent.order_type is TradeType.LIMIT:
            assert intent.limit_price is not None
            return self._limit(intent.side, intent.limit_price, bar, triggered, trigger_ready_at)
        if intent.order_type is TradeType.STOP:
            assert intent.stop_price is not None
            if not triggered:
                if not self._stop_touched(intent.side, intent.stop_price, bar):
                    return FillDecision(False, trigger_ready_at, None, None)
                gap = (
                    bar.open >= intent.stop_price
                    if intent.side is Side.BUY
                    else bar.open <= intent.stop_price
                )
                reference = bar.open if gap else intent.stop_price
                return FillDecision(True, trigger_ready_at, reference, None)
            return FillDecision(True, trigger_ready_at, bar.open, None)

        assert intent.limit_price is not None and intent.stop_price is not None
        if not triggered:
            if self._stop_touched(intent.side, intent.stop_price, bar):
                return FillDecision(True, bar.end, None, intent.limit_price)
            return FillDecision(False, trigger_ready_at, None, intent.limit_price)
        if trigger_ready_at is not None and bar.start < trigger_ready_at:
            return FillDecision(True, trigger_ready_at, None, intent.limit_price)
        return self._limit(intent.side, intent.limit_price, bar, True, trigger_ready_at)

    @staticmethod
    def _stop_touched(side: Side, stop: Decimal, bar: Bar) -> bool:
        if side is Side.BUY:
            return bar.open >= stop or bar.high >= stop
        return bar.open <= stop or bar.low <= stop

    def _limit(
        self,
        side: Side,
        limit: Decimal,
        bar: Bar,
        triggered: bool,
        trigger_ready_at: datetime | None,
    ) -> FillDecision:
        if side is Side.BUY:
            if bar.open <= limit:
                return FillDecision(triggered, trigger_ready_at, bar.open, limit)
            if bar.low <= limit:
                return FillDecision(triggered, trigger_ready_at, limit, limit)
        else:
            if bar.open >= limit:
                return FillDecision(triggered, trigger_ready_at, bar.open, limit)
            if bar.high >= limit:
                return FillDecision(triggered, trigger_ready_at, limit, limit)
        return FillDecision(triggered, trigger_ready_at, None, limit)
