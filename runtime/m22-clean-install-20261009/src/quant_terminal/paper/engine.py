"""Durable, deterministic paper execution with transactional event replay."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Context, Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import cast
from uuid import uuid4

from quant_terminal.accounting.ledger import PortfolioLedger
from quant_terminal.application.runtime import _integer, _object, _stamp, _text
from quant_terminal.backtesting.strategies import StrategyContext
from quant_terminal.core.enums import OrderStatus
from quant_terminal.core.exceptions import DomainError
from quant_terminal.data.model import Bar
from quant_terminal.execution.simulator import HistoricalBroker
from quant_terminal.experiments import canonical_json
from quant_terminal.orders.models import OrderIntent, Side, TradeType, to_ledger_fill

from .clock import LiveClock
from .feed import RealtimeDataFeed
from .schema import SessionConfig, bar, config, observed


def _wire(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _wire(asdict(value))
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    if isinstance(value, dict):
        return {key: _wire(item) for key, item in value.items()}
    return json.loads(canonical_json(value))


def _hash(value: object) -> str:
    return sha256(canonical_json(value)).hexdigest()


def _bar_wire(value: object) -> dict[str, object]:
    data = cast(dict[str, object], _wire(value))
    identity = cast(dict[str, object], data["instrument_id"])
    data["instrument_id"] = identity["value"]
    return data


class _ConflictingEvent(DomainError):
    pass


class PaperBroker(HistoricalBroker):
    """Same OHLC matching and order lifecycle, used with realtime observations."""


class _State:
    def __init__(self, settings: SessionConfig, clock: LiveClock) -> None:
        self.settings = settings
        self.feed = RealtimeDataFeed(settings.instrument, clock)
        self.broker = PaperBroker(settings.execution)
        self.ledger = PortfolioLedger(
            initial_cash=settings.accounting.initial_cash, currency=settings.accounting.currency
        )
        self.connected = False
        self.reason: str | None = "reconciliation_required"
        self.cursor = 0
        self.last_observed: datetime | None = None
        self.broker.set_funds_guard(self._funds_guard)

    def _reserve(self, intent: OrderIntent, quantity: Decimal) -> Decimal:
        price = intent.limit_price or intent.max_execution_price
        if price is None:
            raise DomainError("Paper buy requires a finite execution price cap")
        state = next(
            (
                order
                for order in self.broker.orders.snapshots()
                if order.intent.order_id == intent.order_id
            ),
            None,
        )
        prior_qty = Decimal(0) if state is None else state.filled_quantity
        paid = (
            Decimal(0)
            if state is None
            else sum((fill.commission for fill in state.fills), Decimal(0))
        )
        return price * quantity + self.settings.execution.commission.incremental_fee(
            prior_qty, quantity, paid
        )

    def _active(self) -> tuple[object, ...]:
        return tuple(
            order
            for order in self.broker.orders.snapshots()
            if order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
        )

    def _funds_guard(
        self, intent: OrderIntent, price: Decimal, quantity: Decimal, fee: Decimal, at: datetime
    ) -> bool:
        with localcontext(Context(prec=34)):
            state = self.broker.order(intent.order_id)
            cumulative = sum((fill.quantity * fill.price for fill in state.fills), Decimal(0))
            if cumulative + price * quantity > self.settings.accounting.max_order_notional:
                return False
            if intent.side is Side.BUY:
                others = sum(
                    (
                        self._reserve(order.intent, order.remaining_quantity)
                        for order in self.broker.orders.snapshots()
                        if order.intent.order_id != intent.order_id
                        and order.intent.side is Side.BUY
                        and order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
                    ),
                    Decimal(0),
                )
                if intent.max_execution_price is not None and price > intent.max_execution_price:
                    return False
                return self.ledger.snapshot().cash - others >= price * quantity + fee
            position = next(
                (
                    item.quantity
                    for item in self.ledger.snapshot().positions
                    if item.instrument_id == intent.instrument.instrument_id
                ),
                Decimal(0),
            )
            return quantity <= position

    def reconcile(self) -> dict[str, object]:
        portfolio = self.ledger.snapshot()
        return {
            "cash": str(portfolio.cash),
            "positions": {
                item.instrument_id.value: str(item.quantity)
                for item in portfolio.positions
                if item.quantity != 0
            },
            "orders": {
                item.intent.order_id.value: {
                    "status": item.status.value,
                    "filled_quantity": str(item.filled_quantity),
                    "remaining_quantity": str(item.remaining_quantity),
                }
                for item in self.broker.orders.snapshots()
            },
            "fill_ids": [
                fill.fill_id.value for item in self.broker.orders.snapshots() for fill in item.fills
            ],
            "last_sequence": self.cursor,
        }

    def snapshot(self) -> dict[str, object]:
        return {
            "mode": "paper",
            "broker_orders_enabled": False,
            "connected": self.connected,
            "reason": self.reason,
            "last_sequence": self.cursor,
            "portfolio": _wire(asdict(self.ledger.snapshot())),
            "orders": _wire(self.broker.orders.snapshots()),
            "reconciliation": self.reconcile(),
        }

    def apply(self, event: dict[str, object], *, propose: bool = False) -> None:
        kind, at = event["kind"], observed(event["observed_at"])
        if self.last_observed is not None and at < self.last_observed:
            raise DomainError("Paper command time cannot move backwards")
        if kind == "reconnect":
            if event["snapshot"] != self.reconcile():
                raise DomainError("Paper broker reconciliation mismatch")
            self.connected, self.reason = True, None
        elif kind == "disconnect" or kind == "fault":
            self.connected, self.reason = False, _text(event["reason"])
        elif kind == "bar":
            if not self.connected:
                raise DomainError("Paper session requires reconnection and reconciliation")
            item = bar(event["bar"])
            if _integer(event["sequence"]) != self.cursor + 1:
                raise DomainError("Paper sequence must be contiguous")
            self.feed.append(item)
            fills = self.broker.process_bar(item, at)
            for fill in fills:
                self.ledger.apply_fill(to_ledger_fill(fill))
            if any(
                position.instrument_id == item.instrument_id and position.quantity != 0
                for position in self.ledger.snapshot().positions
            ):
                self.ledger.mark(item.instrument_id, item.close)
            if propose:
                context = StrategyContext(
                    at,
                    self.ledger.snapshot(),
                    tuple(
                        order
                        for order in self.broker.orders.snapshots()
                        if order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
                    ),
                    MappingProxyType({item.instrument_id: self.feed}),
                    MappingProxyType({item.instrument_id: self.settings.instrument}),
                )
                intents = self.settings.strategy.on_bar(context, item)
                event["intents"] = _wire(intents)
            raw_intents = event["intents"]
            if not isinstance(raw_intents, list) or len(raw_intents) > 256:
                raise DomainError("Invalid persisted paper intents")
            for raw in raw_intents:
                self._submit(self._intent(raw), at, item.close)
            self.cursor += 1
        else:
            raise DomainError("Unknown persisted paper command")
        self.last_observed = at
        if self.ledger.snapshot().reconciliation_error != 0:
            raise DomainError("Paper accounting reconciliation failed")

    def _intent(self, raw: object) -> OrderIntent:
        fields = {
            "order_id",
            "instrument",
            "side",
            "order_type",
            "quantity",
            "created_at",
            "expires_at",
            "limit_price",
            "stop_price",
            "max_execution_price",
        }
        data = _object(raw, fields, fields)
        from quant_terminal.core.identifiers import OrderId

        if data["instrument"] != _wire(asdict(self.settings.instrument)):
            raise DomainError("Paper intent instrument mismatch")
        order_id = _object(data["order_id"], {"value"}, {"value"})
        return OrderIntent(
            OrderId(_text(order_id["value"])),
            self.settings.instrument,
            Side(_text(data["side"])),
            TradeType(_text(data["order_type"])),
            Decimal(_text(data["quantity"])),
            _stamp(data["created_at"]),
            limit_price=None
            if data["limit_price"] is None
            else Decimal(_text(data["limit_price"])),
            stop_price=None if data["stop_price"] is None else Decimal(_text(data["stop_price"])),
            expires_at=None if data["expires_at"] is None else _stamp(data["expires_at"]),
            max_execution_price=None
            if data["max_execution_price"] is None
            else Decimal(_text(data["max_execution_price"])),
        )

    def _submit(self, intent: OrderIntent, at: datetime, close: Decimal) -> None:
        with localcontext(Context(prec=34)):
            reference = max(
                value
                for value in (
                    close,
                    intent.limit_price,
                    intent.stop_price,
                    intent.max_execution_price,
                )
                if value is not None
            )
            reason = None
            if intent.created_at != at:
                raise DomainError("Paper strategy order timestamp must equal observation time")
            if reference * intent.quantity > self.settings.accounting.max_order_notional:
                reason = "order notional exceeds configured limit"
            elif intent.side is Side.BUY:
                reserved = sum(
                    (
                        self._reserve(order.intent, order.remaining_quantity)
                        for order in self.broker.orders.snapshots()
                        if order.intent.side is Side.BUY
                        and order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
                    ),
                    Decimal(0),
                )
                if self._reserve(intent, intent.quantity) + reserved > self.ledger.snapshot().cash:
                    reason = "buy exceeds available cash after commitments"
            else:
                held = next(
                    (
                        item.quantity
                        for item in self.ledger.snapshot().positions
                        if item.instrument_id == intent.instrument.instrument_id
                    ),
                    Decimal(0),
                )
                pending = sum(
                    (
                        order.remaining_quantity
                        for order in self.broker.orders.snapshots()
                        if order.intent.side is Side.SELL
                        and order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
                    ),
                    Decimal(0),
                )
                if intent.quantity > held - pending:
                    reason = "sell exceeds available long inventory"
            if reason is None:
                self.broker.submit(intent, at)
            else:
                self.broker.reject_intent(intent, at, reason)


class PaperSession:
    """Single SQLite writer transaction per command; recovery never calls strategies."""

    def __init__(self, path: Path, *, clock: LiveClock | None = None) -> None:
        self.path = Path(path)
        self.clock = clock or LiveClock()
        if not self.path.is_file():
            raise DomainError("Paper session not found")
        # New process/object has to reconcile even if the last process was connected.
        self._reconciled = False
        self._read()

    @classmethod
    def create(
        cls, path: Path, payload: dict[str, object], *, clock: LiveClock | None = None
    ) -> PaperSession:
        config(payload)  # Strict validation happens before creating durable state.
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        try:
            with sqlite3.connect(path) as db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("CREATE TABLE settings (payload TEXT NOT NULL, digest TEXT NOT NULL)")
                db.execute("CREATE TABLE head (count INTEGER NOT NULL, digest TEXT NOT NULL)")
                db.execute("INSERT INTO head VALUES (?, ?)", (0, _hash(payload)))
                db.execute(
                    "CREATE TABLE events (id INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, "
                    "request_hash TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, "
                    "projection TEXT NOT NULL)"
                )
                db.execute(
                    "INSERT INTO settings VALUES (?, ?)",
                    (canonical_json(payload).decode(), _hash(payload)),
                )
        except BaseException:
            # Retain interrupted files; an incomplete database rejects on open.
            raise
        return cls(path, clock=clock)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=rw", uri=True, timeout=10)
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise DomainError("Unsupported paper database schema version")
            db.execute("PRAGMA synchronous=FULL")
            with db:
                yield db
        finally:
            db.close()

    def _restore(self, db: sqlite3.Connection) -> tuple[_State, str, int]:
        rows = db.execute("SELECT payload, digest FROM settings").fetchall()
        if len(rows) != 1:
            raise DomainError("Paper settings missing")
        row = rows[0]
        payload = json.loads(row[0])
        if _hash(payload) != row[1]:
            raise DomainError("Paper settings integrity failure")
        state, chain, count = _State(config(payload), self.clock), row[1], 0
        for identity, event_id, request_hash, raw, digest, projection in db.execute(
            "SELECT id, event_id, request_hash, payload, digest, projection FROM events ORDER BY id"
        ):
            if (
                identity != count + 1
                or _hash(
                    {
                        "previous": chain,
                        "event_id": event_id,
                        "request_hash": request_hash,
                        "payload": json.loads(raw),
                        "projection": projection,
                    }
                )
                != digest
            ):
                raise DomainError("Paper journal integrity failure")
            state.apply(json.loads(raw))
            if _hash(state.snapshot()) != projection:
                raise DomainError("Paper reconstruction differs from committed accounting")
            chain, count = digest, count + 1
        heads = db.execute("SELECT count, digest FROM head").fetchall()
        if heads != [(count, chain)]:
            raise DomainError("Paper journal head integrity failure")
        return state, chain, count

    def _read(self) -> _State:
        try:
            with self._connection() as db:
                db.execute("BEGIN")
                return self._restore(db)[0]
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise DomainError("Paper session persistence is invalid") from exc

    def snapshot(self) -> dict[str, object]:
        state = self._read()
        value = state.snapshot()
        if not self._reconciled:
            value["connected"], value["reason"] = False, state.reason or "reconciliation_required"
        return value

    def _commit(
        self,
        identity: str,
        request: dict[str, object],
        builder: Callable[[_State], dict[str, object]],
    ) -> dict[str, object]:
        _text(identity)
        if len(identity) > 128:
            raise DomainError("Paper event identity exceeds 128 characters")
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                state, chain, count = self._restore(db)
                prior = db.execute(
                    "SELECT request_hash FROM events WHERE event_id=?", (identity,)
                ).fetchone()
                if prior is not None:
                    if prior[0] != _hash(request):
                        raise _ConflictingEvent("Conflicting duplicate paper event")
                    return {"duplicate": True, **self.snapshot()}
                if count >= 10000:
                    raise DomainError("Paper session command limit reached")
                event = builder(state)
                state.apply(event, propose=event["kind"] == "bar")
                projection = _hash(state.snapshot())
                digest = _hash(
                    {
                        "previous": chain,
                        "event_id": identity,
                        "request_hash": _hash(request),
                        "payload": event,
                        "projection": projection,
                    }
                )
                db.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        count + 1,
                        identity,
                        _hash(request),
                        canonical_json(event).decode(),
                        digest,
                        projection,
                    ),
                )
                db.execute("UPDATE head SET count=?, digest=?", (count + 1, digest))
            if event["kind"] == "reconnect":
                self._reconciled = True
            if event["kind"] in ("disconnect", "fault"):
                self._reconciled = False
            return {"duplicate": False, **self.snapshot()}
        except _ConflictingEvent:
            self._halt("conflicting_duplicate_event", request)
            raise DomainError("Conflicting duplicate paper event; session halted") from None
        except (sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise DomainError("Paper event rejected; no accounting changes committed") from exc

    def reconnect(self, event_id: str, snapshot: dict[str, object]) -> dict[str, object]:
        if snapshot != self._read().reconcile():
            self._halt("reconciliation_mismatch", snapshot)
            raise DomainError("Paper broker reconciliation mismatch")
        return self._commit(
            event_id,
            {"kind": "reconnect", "snapshot": snapshot},
            lambda state: {
                "kind": "reconnect",
                "snapshot": snapshot,
                "observed_at": self.clock.now.isoformat(),
            },
        )

    def disconnect(self, event_id: str, reason: str = "disconnected") -> dict[str, object]:
        return self._commit(
            event_id,
            {"kind": "disconnect", "reason": reason},
            lambda state: {
                "kind": "disconnect",
                "reason": reason,
                "observed_at": self.clock.now.isoformat(),
            },
        )

    def ingest(self, event_id: str, sequence: int, value: Bar) -> dict[str, object]:
        request = {"kind": "bar", "sequence": sequence, "bar": _bar_wire(asdict(value))}
        fault: list[str] = []

        def build(state: _State) -> dict[str, object]:
            if not self._reconciled or not state.connected:
                raise DomainError("Paper session requires reconnection and reconciliation")
            now = self.clock.now
            if type(sequence) is not int or sequence != state.cursor + 1:
                fault.append("missing_or_out_of_order_update")
            elif value.available_at > now:
                fault.append("future_market_data")
            elif (now - value.end).total_seconds() > state.settings.stale_seconds:
                fault.append("stale_market_data")
            elif value.instrument_id != state.settings.instrument.instrument_id:
                fault.append("unconfigured_instrument")
            elif state.feed.bars and value.start < state.feed.bars[-1].end:
                fault.append("out_of_order_market_data")
            if fault:
                return {"kind": "fault", "reason": fault[0], "observed_at": now.isoformat()}
            return {**request, "observed_at": now.isoformat(), "intents": []}

        result = self._commit(event_id, request, build)
        if fault:
            raise DomainError(f"Paper session halted: {fault[0]}")
        return result

    def confirm_fill(self, value: dict[str, object]) -> dict[str, object]:
        """Duplicate transport acknowledgement only; matching engine owns all fills."""
        for order in self._read().broker.orders.snapshots():
            for fill in order.fills:
                if _wire(asdict(fill)) == value:
                    return {"duplicate": True, **self.snapshot()}
        self._halt("unknown_or_conflicting_fill", value)
        raise DomainError("Unknown or conflicting paper fill; reconcile before proceeding")

    def _halt(self, reason: str, evidence: object) -> None:
        self._reconciled = False
        request: dict[str, object] = {
            "kind": "fault",
            "reason": reason,
            "evidence_hash": _hash(evidence),
        }
        self._commit(
            "fault-" + uuid4().hex,
            request,
            lambda state: {
                "kind": "fault",
                "reason": reason,
                "observed_at": self.clock.now.isoformat(),
            },
        )
