"""Historical OHLC broker with deterministic capacity, prices and lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    localcontext,
)
from hashlib import sha256

from quant_terminal.core.clock import utc
from quant_terminal.core.enums import OrderStatus
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import FillId, InstrumentId, OrderId
from quant_terminal.data.model import Bar, InstrumentMeta
from quant_terminal.execution.fills import FillDecision, FillModel, OHLCFillModel
from quant_terminal.execution.models import ExecutionConfig
from quant_terminal.orders.lifecycle import OrderManager
from quant_terminal.orders.models import Fill, OrderIntent, OrderSnapshot, Side


def _context() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN)


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


def _round_tick(value: Decimal, tick: Decimal, side: Side) -> Decimal:
    with localcontext(_context()):
        rounding = ROUND_CEILING if side is Side.BUY else ROUND_FLOOR
        units = (value / tick).to_integral_value(rounding=rounding)
        return units * tick


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    with localcontext(_context()):
        return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


class HistoricalBroker:
    """Simulate fills from completed bars; no external broker connectivity."""

    def __init__(
        self, config: ExecutionConfig | None = None, fill_model: FillModel | None = None
    ) -> None:
        if config is None:
            config = ExecutionConfig(zero_cost_assumption="ZERO_COST_ASSUMPTION")
        if not isinstance(config, ExecutionConfig):
            raise DomainError("ExecutionConfig is required")
        if fill_model is None:
            fill_model = OHLCFillModel()
        if not isinstance(fill_model, FillModel):
            raise DomainError("fill_model must implement match")
        self.config = config
        self.fill_model = fill_model
        self.orders = OrderManager()
        self._instruments: dict[InstrumentId, InstrumentMeta] = {}
        self._processed_bars: set[tuple[InstrumentId, str, datetime]] = set()
        self._last_bar_start: dict[tuple[InstrumentId, str], datetime] = {}
        self._frequency_by_instrument: dict[InstrumentId, str] = {}
        self._last_observed_at: datetime | None = None
        self._last_event_at: datetime | None = None
        self._funds_guard: (
            Callable[[OrderIntent, Decimal, Decimal, Decimal, datetime], bool] | None
        ) = None

    def submit(self, intent: OrderIntent, accepted_at: datetime | None = None) -> OrderSnapshot:
        if not isinstance(intent, OrderIntent):
            raise DomainError("OrderIntent is required")
        if intent.order_id in self.orders._orders:
            raise DomainError("Duplicate order_id")
        existing = self._instruments.get(intent.instrument.instrument_id)
        if existing is not None and existing != intent.instrument:
            raise DomainError("Instrument metadata changed within an execution simulator")
        accepted = utc(accepted_at or intent.created_at)
        if self._last_event_at is not None and accepted < self._last_event_at:
            raise DomainError("Order acceptance cannot be backdated")
        if intent.expires_at is not None and accepted >= intent.expires_at:
            raise DomainError("Order must be accepted before it expires")
        snapshot = self.orders.submit(intent, accepted)
        self._instruments[intent.instrument.instrument_id] = intent.instrument
        self._last_event_at = accepted
        return snapshot

    def order(self, order_id: OrderId) -> OrderSnapshot:
        return self.orders.snapshot(order_id)

    def cancel(
        self, order_id: OrderId, timestamp: datetime, reason: str | None = None
    ) -> OrderSnapshot:
        event_time = self._check_event_time(timestamp)
        snapshot = self.orders.cancel(order_id, event_time, reason)
        self._last_event_at = event_time
        return snapshot

    def reject(self, order_id: OrderId, timestamp: datetime, reason: str) -> OrderSnapshot:
        event_time = self._check_event_time(timestamp)
        snapshot = self.orders.reject(order_id, event_time, reason)
        self._last_event_at = event_time
        return snapshot

    def reject_intent(self, intent: OrderIntent, timestamp: datetime, reason: str) -> OrderSnapshot:
        if not isinstance(intent, OrderIntent):
            raise DomainError("OrderIntent is required")
        event_time = self._check_event_time(timestamp)
        snapshot = self.orders.reject_new(intent, event_time, reason)
        self._last_event_at = event_time
        return snapshot

    def _check_event_time(self, timestamp: datetime) -> datetime:
        event_time = utc(timestamp)
        if self._last_event_at is not None and event_time < self._last_event_at:
            raise DomainError("Order events cannot be backdated")
        return event_time

    def process_bar(self, bar: Bar, observed_at: datetime) -> tuple[Fill, ...]:
        if not isinstance(bar, Bar):
            raise DomainError("Bar is required")
        observed = utc(observed_at)
        if observed < bar.available_at:
            raise DomainError("Bar cannot be executed before it is observable")
        if self._last_observed_at is not None and observed < self._last_observed_at:
            raise DomainError("Execution observations must be monotonic")
        if self._last_event_at is not None and observed < self._last_event_at:
            raise DomainError("Market data cannot be observed before the latest order event")
        key = (bar.instrument_id, bar.frequency, bar.start)
        if key in self._processed_bars:
            raise DomainError("A market bar may only be processed once")
        stream = (bar.instrument_id, bar.frequency)
        frequency = self._frequency_by_instrument.get(bar.instrument_id)
        if frequency is not None and frequency != bar.frequency:
            raise DomainError("Use one execution frequency per instrument in a broker run")
        previous_start = self._last_bar_start.get(stream)
        if previous_start is not None and bar.start <= previous_start:
            raise DomainError("Market bars must be processed in increasing time order")
        self._processed_bars.add(key)
        self._last_bar_start[stream] = bar.start
        self._frequency_by_instrument[bar.instrument_id] = bar.frequency
        self._last_observed_at = observed
        self._last_event_at = observed

        instrument = self._instruments.get(bar.instrument_id)
        quantity_step = instrument.quantity_step if instrument is not None else Decimal(1)
        with localcontext(_context()):
            capacity = (
                _floor_step(bar.volume * self.config.maximum_participation, quantity_step)
                if bar.volume > 0
                else Decimal(0)
            )
        created: list[Fill] = []
        candidates = sorted(
            (
                state
                for state in self.orders._orders.values()
                if state.intent.instrument.instrument_id == bar.instrument_id
                and state.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
            ),
            key=lambda state: (
                state.accepted_at or state.intent.created_at,
                state.intent.order_id.value,
            ),
        )
        for state in candidates:
            intent = state.intent
            if intent.expires_at is not None and bar.start >= intent.expires_at:
                self.orders.expire(intent.order_id, observed)
                continue
            if state.accepted_at is None or state.accepted_at > bar.start:
                continue
            decision = self.fill_model.match(
                intent,
                bar,
                triggered=state.stop_triggered,
                trigger_ready_at=state.trigger_ready_at,
            )
            if not isinstance(decision, FillDecision):
                raise DomainError("Fill model returned an invalid decision")
            if decision.protected_limit is not None and not _multiple(
                decision.protected_limit, intent.instrument.tick_size
            ):
                raise DomainError("Fill model price protection violates instrument tick size")
            state.stop_triggered = decision.triggered
            state.trigger_ready_at = decision.trigger_ready_at
            if decision.reference_price is None or capacity <= 0:
                continue
            if not decision.reference_price.is_finite() or decision.reference_price <= 0:
                self.orders.reject(
                    intent.order_id, observed, "fill model returned an invalid price"
                )
                continue
            with localcontext(_context()):
                remaining = intent.quantity - state.filled_quantity
            quantity = _floor_step(min(remaining, capacity), intent.instrument.quantity_step)
            if quantity <= 0:
                continue
            with localcontext(_context()):
                participation = quantity / bar.volume
            price = self._fill_price(
                intent,
                decision.reference_price,
                intent.limit_price if intent.limit_price is not None else decision.protected_limit,
                participation,
            )
            if price <= 0:
                self.orders.reject(intent.order_id, observed, "slippage produced nonpositive price")
                continue
            with localcontext(_context()):
                fee = self.config.commission.incremental_fee(
                    state.filled_quantity, quantity, state.commission_paid
                )
            if not isinstance(fee, Decimal) or not fee.is_finite() or fee < 0:
                raise DomainError("Commission model returned an invalid fee")
            if self._funds_guard is not None:
                if not self._funds_guard(intent, price, quantity, fee, observed):
                    self.orders.reject(
                        intent.order_id,
                        observed,
                        "fill exceeds available cash after commitments",
                    )
                    created[:] = [fill for fill in created if fill.order_id != intent.order_id]
                    continue
            fill = Fill(
                fill_id=FillId(
                    "fill-"
                    + sha256(f"{intent.order_id.value}:{len(state.fills) + 1}".encode()).hexdigest()
                ),
                order_id=intent.order_id,
                instrument=intent.instrument,
                side=intent.side,
                quantity=quantity,
                reference_price=decision.reference_price,
                price=price,
                commission=fee,
                timestamp=bar.start,
                observed_at=observed,
            )
            self.orders._record_fill(state, fill)
            created.append(fill)
            with localcontext(_context()):
                capacity -= quantity
        return tuple(created)

    def set_funds_guard(
        self,
        guard: Callable[[OrderIntent, Decimal, Decimal, Decimal, datetime], bool],
    ) -> None:
        if not callable(guard):
            raise DomainError("funds guard must be callable")
        self._funds_guard = guard

    def _fill_price(
        self,
        intent: OrderIntent,
        reference: Decimal,
        protected_limit: Decimal | None,
        participation: Decimal,
    ) -> Decimal:
        slippage = self.config.slippage.amount(reference, participation)
        if not isinstance(slippage, Decimal) or not slippage.is_finite() or slippage < 0:
            raise DomainError("Slippage model returned an invalid amount")
        with localcontext(_context()):
            raw = reference + slippage if intent.side is Side.BUY else reference - slippage
            if protected_limit is not None:
                if intent.side is Side.BUY:
                    raw = min(raw, protected_limit)
                else:
                    raw = max(raw, protected_limit)
        price = _round_tick(raw, intent.instrument.tick_size, intent.side)
        if protected_limit is not None:
            price = (
                min(price, protected_limit)
                if intent.side is Side.BUY
                else max(price, protected_limit)
            )
        return price
