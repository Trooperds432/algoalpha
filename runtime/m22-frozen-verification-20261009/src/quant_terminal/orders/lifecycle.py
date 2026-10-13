"""Deterministic order state transitions and immutable snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from quant_terminal.core.clock import utc
from quant_terminal.core.enums import OrderStatus
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import OrderId
from quant_terminal.orders.models import Fill, OrderEvent, OrderIntent, OrderSnapshot, Trade

_ALLOWED: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.SUBMITTED, OrderStatus.REJECTED}),
    OrderStatus.SUBMITTED: frozenset(
        {OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.ACCEPTED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
        }
    ),
}
_LIVE = frozenset({OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED})


def _context() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN)


@dataclass(slots=True)
class _OrderState:
    intent: OrderIntent
    accepted_at: datetime | None
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: Decimal = Decimal(0)
    commission_paid: Decimal = Decimal(0)
    events: list[OrderEvent] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    reason: str | None = None
    stop_triggered: bool = False
    trigger_ready_at: datetime | None = None


class OrderManager:
    """Owns order state while retaining an append-only lifecycle event list."""

    def __init__(self) -> None:
        self._orders: dict[OrderId, _OrderState] = {}

    def submit(self, intent: OrderIntent, accepted_at: datetime) -> OrderSnapshot:
        if not isinstance(intent, OrderIntent):
            raise DomainError("OrderIntent is required")
        accepted = utc(accepted_at)
        if accepted < intent.created_at:
            raise DomainError("Order cannot be accepted before it was created")
        if intent.order_id in self._orders:
            raise DomainError("Duplicate order_id")
        state = _OrderState(intent=intent, accepted_at=accepted)
        state.events.append(OrderEvent(intent.order_id, 0, OrderStatus.CREATED, intent.created_at))
        self._orders[intent.order_id] = state
        self._transition(state, OrderStatus.SUBMITTED, accepted)
        self._transition(state, OrderStatus.ACCEPTED, accepted)
        return self.snapshot(intent.order_id)

    def reject_new(self, intent: OrderIntent, timestamp: datetime, reason: str) -> OrderSnapshot:
        if not isinstance(intent, OrderIntent):
            raise DomainError("OrderIntent is required")
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("A rejection reason is required")
        if intent.order_id in self._orders:
            raise DomainError("Duplicate order_id")
        at = utc(timestamp)
        if at < intent.created_at:
            raise DomainError("Order cannot be rejected before it was created")
        state = _OrderState(intent=intent, accepted_at=None)
        state.events.append(OrderEvent(intent.order_id, 0, OrderStatus.CREATED, intent.created_at))
        self._orders[intent.order_id] = state
        self._transition(state, OrderStatus.SUBMITTED, at)
        self._transition(state, OrderStatus.REJECTED, at, reason)
        return self.snapshot(intent.order_id)

    def snapshot(self, order_id: OrderId) -> OrderSnapshot:
        state = self._get(order_id)
        with localcontext(_context()):
            remaining = state.intent.quantity - state.filled_quantity
        trade = None
        if state.status not in _LIVE and state.fills:
            with localcontext(_context()):
                notional = sum((f.quantity * f.price for f in state.fills), Decimal(0))
                average = notional / state.filled_quantity
                commission = sum((f.commission for f in state.fills), Decimal(0))
            trade = Trade(
                order_id=order_id,
                side=state.intent.side,
                quantity=state.filled_quantity,
                average_price=average,
                commission=commission,
                fill_ids=tuple(fill.fill_id for fill in state.fills),
            )
        return OrderSnapshot(
            intent=state.intent,
            status=state.status,
            accepted_at=state.accepted_at,
            filled_quantity=state.filled_quantity,
            remaining_quantity=remaining,
            events=tuple(state.events),
            fills=tuple(state.fills),
            reason=state.reason,
            trade=trade,
        )

    def snapshots(self) -> tuple[OrderSnapshot, ...]:
        return tuple(
            self.snapshot(order_id) for order_id in sorted(self._orders, key=lambda i: i.value)
        )

    def cancel(
        self, order_id: OrderId, timestamp: datetime, reason: str | None = None
    ) -> OrderSnapshot:
        state = self._get(order_id)
        self._transition(state, OrderStatus.CANCELLED, utc(timestamp), reason)
        return self.snapshot(order_id)

    def reject(self, order_id: OrderId, timestamp: datetime, reason: str) -> OrderSnapshot:
        if not isinstance(reason, str) or not reason.strip():
            raise DomainError("A rejection reason is required")
        state = self._get(order_id)
        self._transition(state, OrderStatus.REJECTED, utc(timestamp), reason)
        return self.snapshot(order_id)

    def expire(self, order_id: OrderId, timestamp: datetime) -> OrderSnapshot:
        state = self._get(order_id)
        self._transition(state, OrderStatus.EXPIRED, utc(timestamp), "time in force expired")
        return self.snapshot(order_id)

    def _get(self, order_id: OrderId) -> _OrderState:
        try:
            return self._orders[order_id]
        except KeyError as exc:
            raise DomainError("Unknown order_id") from exc

    def _transition(
        self,
        state: _OrderState,
        status: OrderStatus,
        timestamp: datetime,
        reason: str | None = None,
    ) -> None:
        if state.status not in _ALLOWED or status not in _ALLOWED[state.status]:
            raise DomainError(f"Invalid order transition: {state.status} -> {status}")
        if state.events and timestamp < state.events[-1].timestamp:
            raise DomainError("Order event timestamps must be monotonic")
        state.status = status
        state.reason = reason
        state.events.append(
            OrderEvent(state.intent.order_id, len(state.events), status, timestamp, reason)
        )

    def _record_fill(self, state: _OrderState, fill: Fill) -> None:
        with localcontext(_context()):
            next_quantity = state.filled_quantity + fill.quantity
            next_commission = state.commission_paid + fill.commission
        if next_quantity > state.intent.quantity:
            raise DomainError("Fills cannot exceed order quantity")
        state.fills.append(fill)
        state.filled_quantity = next_quantity
        state.commission_paid = next_commission
        status = (
            OrderStatus.FILLED
            if next_quantity == state.intent.quantity
            else OrderStatus.PARTIALLY_FILLED
        )
        self._transition(state, status, fill.observed_at)
