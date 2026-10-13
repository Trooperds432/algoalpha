"""Fail-closed pretrade checks and durable intent reservations.

This module authorizes synthetic order intents only. It has no broker client and
never transmits an order. A reservation remains exposure until a broker-confirmed
terminal result or explicit reconciliation resolves it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, final

from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId, OrderId
from quant_terminal.orders.models import OrderIntent, Side, TradeType

if TYPE_CHECKING:
    from quant_terminal.execution.brokers.trading212 import ProviderOrderFillAggregate


class ReservationState(StrEnum):
    RESERVED = "reserved"
    UNKNOWN = "unknown"
    ACCEPTED = "accepted"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ResolutionSource(StrEnum):
    BROKER = "broker"
    RECONCILIATION = "reconciliation"


def _duration_seconds(value: timedelta) -> Decimal:
    return Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / Decimal(
        "1000000"
    )


@dataclass(frozen=True, slots=True)
class RiskLimits:
    currency: str
    allowed_instruments: frozenset[InstrumentId]
    max_position_value: Decimal
    max_order_value: Decimal
    max_gross_exposure: Decimal
    max_net_exposure: Decimal
    max_leverage: Decimal
    max_daily_loss: Decimal
    max_drawdown: Decimal
    max_market_data_age_seconds: Decimal
    max_cash_reservation: Decimal
    allowed_trade_types: frozenset[TradeType] = frozenset({TradeType.MARKET, TradeType.LIMIT})
    allow_short: bool = False
    kill_switch: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isalpha()
            or self.currency != self.currency.upper()
        ):
            raise DomainError("risk currency must be a three-letter uppercase code")
        if (
            not isinstance(self.allowed_instruments, frozenset)
            or not self.allowed_instruments
            or any(not isinstance(item, InstrumentId) for item in self.allowed_instruments)
        ):
            raise DomainError("allowed_instruments must be a nonempty frozenset of InstrumentId")
        for name in (
            "max_position_value",
            "max_order_value",
            "max_gross_exposure",
            "max_net_exposure",
            "max_leverage",
            "max_daily_loss",
            "max_drawdown",
            "max_market_data_age_seconds",
            "max_cash_reservation",
        ):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise DomainError(f"{name} must be a positive finite Decimal")
        if self.max_drawdown > 1:
            raise DomainError("max_drawdown must be a fraction no greater than one")
        if (
            not isinstance(self.allowed_trade_types, frozenset)
            or not self.allowed_trade_types
            or any(not isinstance(item, TradeType) for item in self.allowed_trade_types)
        ):
            raise DomainError("allowed_trade_types must be a nonempty frozenset of TradeType")
        if type(self.allow_short) is not bool or type(self.kill_switch) is not bool:
            raise DomainError("risk switches must be booleans")


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    as_of: datetime
    currency: str
    equity: Decimal
    cash: Decimal
    daily_pnl: Decimal
    peak_equity: Decimal
    positions: Mapping[InstrumentId, Decimal]
    market_prices: Mapping[InstrumentId, Decimal]
    market_price_times: Mapping[InstrumentId, datetime]

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", utc(self.as_of))
        if (
            not isinstance(self.currency, str)
            or len(self.currency) != 3
            or not self.currency.isascii()
            or not self.currency.isalpha()
            or self.currency != self.currency.upper()
        ):
            raise DomainError("snapshot currency must be a three-letter uppercase code")
        for name in ("equity", "cash", "daily_pnl", "peak_equity"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise DomainError(f"snapshot {name} must be a finite Decimal")
        if self.equity <= 0 or self.peak_equity < self.equity:
            raise DomainError("equity must be positive and no greater than peak_equity")
        if self.cash < 0:
            raise DomainError("cash cannot be negative in this single-currency risk model")
        for name in ("positions", "market_prices", "market_price_times"):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise DomainError(f"snapshot {name} must be a mapping")
        if any(
            not isinstance(key, InstrumentId)
            or not isinstance(value, Decimal)
            or not value.is_finite()
            for key, value in self.positions.items()
        ):
            raise DomainError("positions must map InstrumentId to finite Decimal quantity")
        if any(
            not isinstance(key, InstrumentId)
            or not isinstance(value, Decimal)
            or not value.is_finite()
            or value <= 0
            for key, value in self.market_prices.items()
        ):
            raise DomainError("market_prices must map InstrumentId to positive finite Decimal")
        if any(
            not isinstance(key, InstrumentId) or not isinstance(value, datetime)
            for key, value in self.market_price_times.items()
        ):
            raise DomainError("market_price_times must map InstrumentId to aware timestamps")
        object.__setattr__(
            self,
            "positions",
            MappingProxyType(dict(self.positions)),
        )
        object.__setattr__(
            self,
            "market_prices",
            MappingProxyType(dict(self.market_prices)),
        )
        object.__setattr__(
            self,
            "market_price_times",
            MappingProxyType({key: utc(value) for key, value in self.market_price_times.items()}),
        )
        if any(timestamp > self.as_of for timestamp in self.market_price_times.values()):
            raise DomainError("market data cannot be observed after the risk snapshot")

    @property
    def content_hash(self) -> str:
        material = {
            "as_of": self.as_of.isoformat(),
            "currency": self.currency,
            "equity": str(self.equity),
            "cash": str(self.cash),
            "daily_pnl": str(self.daily_pnl),
            "peak_equity": str(self.peak_equity),
            "positions": sorted((key.value, str(value)) for key, value in self.positions.items()),
            "market_prices": sorted(
                (key.value, str(value)) for key, value in self.market_prices.items()
            ),
            "market_price_times": sorted(
                (key.value, value.isoformat()) for key, value in self.market_price_times.items()
            ),
        }
        return hashlib.sha256(_canonical_json(material)).hexdigest()


@final
@dataclass(frozen=True, slots=True, init=False)
class ReconciliationEvidence:
    """Immutable proof that exact local state matched a typed broker reconciliation."""

    snapshot_id: str
    snapshot_hash: str
    local_snapshot_hash: str
    observed_at: datetime
    currency: str
    broker_available_cash: Decimal
    broker_positions: Mapping[InstrumentId, Decimal]
    provider_aggregates: Mapping[str, ProviderOrderFillAggregate]
    matches: bool

    @classmethod
    def from_broker_reconciliation(
        cls,
        local_snapshot: RiskSnapshot,
        broker_reconciliation: object,
        *,
        snapshot_id: str,
        ticker_to_instrument: Mapping[str, InstrumentId],
        provider_aggregates: tuple[object, ...],
        observed_at: datetime,
    ) -> ReconciliationEvidence:
        """Bind adapter comparison, broker snapshot identity and order fills together."""
        from quant_terminal.execution.brokers.trading212 import (
            BrokerReconciliation,
            PositionReconciliation,
        )

        if not isinstance(local_snapshot, RiskSnapshot):
            raise DomainError("typed local RiskSnapshot is required")
        if not isinstance(broker_reconciliation, BrokerReconciliation):
            raise DomainError("typed BrokerReconciliation evidence is required")
        if snapshot_id != broker_reconciliation.broker_snapshot_id:
            raise DomainError("snapshot ID must match the broker reconciliation")
        if (
            not isinstance(snapshot_id, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", snapshot_id) is None
        ):
            raise DomainError("broker snapshot ID must be a SHA-256 identity")
        seen_at = utc(observed_at)
        if seen_at < local_snapshot.as_of:
            raise DomainError("broker reconciliation predates the local snapshot")
        if not isinstance(ticker_to_instrument, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, InstrumentId)
            for key, value in ticker_to_instrument.items()
        ):
            raise DomainError("ticker_to_instrument must map broker tickers to InstrumentId")
        from quant_terminal.execution.brokers.trading212 import ProviderOrderFillAggregate

        if not isinstance(provider_aggregates, tuple):
            raise DomainError("typed provider fill aggregates are required")
        aggregates: dict[str, ProviderOrderFillAggregate] = {}
        for aggregate in provider_aggregates:
            if (
                type(aggregate) is not ProviderOrderFillAggregate
                or not aggregate.verify_hash()
                or aggregate.local_order_id.value in aggregates
            ):
                raise DomainError("provider fill aggregate is untyped, duplicate, or invalid")
            aggregates[aggregate.local_order_id.value] = aggregate

        rows = broker_reconciliation.positions
        tickers = {row.ticker for row in rows}
        if set(ticker_to_instrument) != tickers:
            raise DomainError("broker reconciliation ticker mapping is incomplete")
        broker_positions: dict[InstrumentId, Decimal] = {}
        local_positions: dict[InstrumentId, Decimal] = {}
        positions_match = True
        for row in rows:
            if not isinstance(row, PositionReconciliation):
                raise DomainError("broker reconciliation contains an untyped position row")
            if (
                not isinstance(row.local_quantity, Decimal)
                or not row.local_quantity.is_finite()
                or not isinstance(row.broker_quantity, Decimal)
                or not row.broker_quantity.is_finite()
                or type(row.matches) is not bool
            ):
                raise DomainError("broker position reconciliation row is malformed")
            instrument_id = ticker_to_instrument[row.ticker]
            local_positions[instrument_id] = row.local_quantity
            broker_positions[instrument_id] = row.broker_quantity
            equal = row.local_quantity == row.broker_quantity
            positions_match = positions_match and equal and row.matches is equal
        local_positions = {key: value for key, value in local_positions.items() if value != 0}
        broker_positions = {key: value for key, value in broker_positions.items() if value != 0}
        snapshot_positions = {
            key: value for key, value in local_snapshot.positions.items() if value != 0
        }
        cash_match = (
            isinstance(broker_reconciliation.local_available_cash, Decimal)
            and isinstance(broker_reconciliation.broker_available_cash, Decimal)
            and broker_reconciliation.local_available_cash == local_snapshot.cash
            and broker_reconciliation.local_available_cash
            == broker_reconciliation.broker_available_cash
            and type(broker_reconciliation.cash_matches) is bool
            and broker_reconciliation.cash_matches is True
        )
        row_matches = all(row.matches for row in rows)
        matches = (
            broker_reconciliation.currency == local_snapshot.currency
            and local_positions == snapshot_positions
            and broker_positions == snapshot_positions
            and positions_match
            and cash_match
            and broker_reconciliation.cash_matches is cash_match
            and broker_reconciliation.matches is (cash_match and row_matches)
            and broker_reconciliation.matches is True
        )
        broker_material = {
            "snapshot_id": snapshot_id,
            "observed_at": seen_at.isoformat(),
            "currency": broker_reconciliation.currency,
            "cash": str(broker_reconciliation.broker_available_cash),
            "positions": sorted((key.value, str(value)) for key, value in broker_positions.items()),
            "aggregates": sorted((key, value.aggregate_id) for key, value in aggregates.items()),
            "local_snapshot_hash": local_snapshot.content_hash,
            "matches": matches,
        }
        instance = object.__new__(cls)
        object.__setattr__(instance, "snapshot_id", snapshot_id.strip())
        object.__setattr__(
            instance, "snapshot_hash", hashlib.sha256(_canonical_json(broker_material)).hexdigest()
        )
        object.__setattr__(instance, "local_snapshot_hash", local_snapshot.content_hash)
        object.__setattr__(instance, "observed_at", seen_at)
        object.__setattr__(instance, "currency", broker_reconciliation.currency)
        object.__setattr__(
            instance, "broker_available_cash", broker_reconciliation.broker_available_cash
        )
        object.__setattr__(instance, "broker_positions", MappingProxyType(broker_positions))
        object.__setattr__(instance, "provider_aggregates", MappingProxyType(aggregates))
        object.__setattr__(instance, "matches", matches)
        return instance

    def verify_hash(self) -> bool:
        material = {
            "snapshot_id": self.snapshot_id,
            "observed_at": self.observed_at.isoformat(),
            "currency": self.currency,
            "cash": str(self.broker_available_cash),
            "positions": sorted(
                (key.value, str(value)) for key, value in self.broker_positions.items()
            ),
            "aggregates": sorted(
                (key, value.aggregate_id) for key, value in self.provider_aggregates.items()
            ),
            "local_snapshot_hash": self.local_snapshot_hash,
            "matches": self.matches,
        }
        return hashlib.sha256(_canonical_json(material)).hexdigest() == self.snapshot_hash


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, slots=True)
class IntentReservation:
    order_id: str
    state: ReservationState
    instrument_id: InstrumentId
    side: Side
    quantity: Decimal
    reference_price: Decimal
    reserved_cash: Decimal
    created_at: datetime
    updated_at: datetime
    filled_quantity: Decimal = Decimal("0")
    reconciled_quantity: Decimal = Decimal("0")

    @property
    def outstanding_quantity(self) -> Decimal:
        if self.state in (
            ReservationState.RESERVED,
            ReservationState.UNKNOWN,
            ReservationState.ACCEPTED,
        ):
            return self.quantity - self.reconciled_quantity
        return self.filled_quantity - self.reconciled_quantity


@dataclass(frozen=True, slots=True)
class KillSwitchStatus:
    active: bool
    configured_lock: bool
    updated_at: datetime
    operator: str
    reason: str


class PreTradeRisk:
    """SQLite-backed pretrade gate with cross-process serialized reservations."""

    def __init__(self, database: Path, limits: RiskLimits) -> None:
        self.path = Path(database)
        self.limits = limits
        if self.path.exists() and self.path.is_symlink():
            raise DomainError("risk database cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._connect()) as connection:
                # Serialize the complete create/migrate sequence across processes.
                # Without the write lock, parallel constructors can observe the
                # same pre-migration columns and both attempt the same ALTER TABLE.
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS risk_reservations (
                        order_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL CHECK(state IN (
                            'reserved','unknown','accepted','filled','cancelled','rejected'
                        )),
                        instrument_id TEXT NOT NULL,
                        currency TEXT NOT NULL,
                        side TEXT NOT NULL CHECK(side IN ('buy','sell')),
                        quantity TEXT NOT NULL,
                        reference_price TEXT NOT NULL,
                        reserved_cash TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        resolution_source TEXT
                    )"""
                )
                columns = {
                    row["name"]
                    for row in connection.execute("PRAGMA table_info(risk_reservations)")
                }
                if "filled_quantity" not in columns:
                    connection.execute(
                        """ALTER TABLE risk_reservations ADD COLUMN filled_quantity
                        TEXT NOT NULL DEFAULT '0'"""
                    )
                if "reconciled_quantity" not in columns:
                    connection.execute(
                        """ALTER TABLE risk_reservations ADD COLUMN reconciled_quantity
                        TEXT NOT NULL DEFAULT '0'"""
                    )
                if "order_type" not in columns:
                    connection.execute(
                        """ALTER TABLE risk_reservations ADD COLUMN order_type
                        TEXT NOT NULL DEFAULT 'market'"""
                    )
                if "limit_price" not in columns:
                    connection.execute("ALTER TABLE risk_reservations ADD COLUMN limit_price TEXT")
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS risk_provider_bindings (
                        local_order_id TEXT PRIMARY KEY REFERENCES risk_reservations(order_id),
                        broker_order_id TEXT NOT NULL UNIQUE,
                        provider_order_id TEXT NOT NULL UNIQUE,
                        ticker TEXT NOT NULL,
                        instrument_id TEXT NOT NULL,
                        side TEXT NOT NULL CHECK(side IN ('buy','sell')),
                        quantity TEXT NOT NULL,
                        limit_price TEXT NOT NULL,
                        mapped_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS risk_fills (
                        fill_id TEXT PRIMARY KEY,
                        order_id TEXT NOT NULL REFERENCES risk_reservations(order_id),
                        quantity TEXT NOT NULL,
                        filled_at TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS risk_reconciliations (
                        reconciliation_id TEXT PRIMARY KEY,
                        order_id TEXT NOT NULL REFERENCES risk_reservations(order_id),
                        cumulative_filled_quantity TEXT NOT NULL,
                        reconciled_at TEXT NOT NULL,
                        source TEXT NOT NULL CHECK(source='reconciliation')
                    )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS risk_control (
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                        kill_switch_active INTEGER NOT NULL CHECK(kill_switch_active IN (0,1)),
                        updated_at TEXT NOT NULL,
                        operator TEXT NOT NULL,
                        reason TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS kill_switch_events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        active INTEGER NOT NULL CHECK(active IN (0,1)),
                        changed_at TEXT NOT NULL,
                        operator TEXT NOT NULL,
                        reason TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE TRIGGER IF NOT EXISTS kill_switch_events_no_update
                    BEFORE UPDATE ON kill_switch_events
                    BEGIN SELECT RAISE(ABORT, 'kill switch history is append-only'); END"""
                )
                connection.execute(
                    """CREATE TRIGGER IF NOT EXISTS kill_switch_events_no_delete
                    BEFORE DELETE ON kill_switch_events
                    BEGIN SELECT RAISE(ABORT, 'kill switch history is append-only'); END"""
                )
                connection.execute(
                    """INSERT OR IGNORE INTO risk_control
                    VALUES (1, ?, '1970-01-01T00:00:00+00:00', 'configuration',
                    'initial risk policy')""",
                    (int(limits.kill_switch),),
                )
                self._check_integrity(connection)
                connection.commit()
        except sqlite3.DatabaseError as exc:
            raise DomainError("risk reservation database is unavailable or corrupt") from exc

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _check_integrity(connection: sqlite3.Connection) -> None:
        result = connection.execute("PRAGMA quick_check").fetchone()
        if result is None or result[0] != "ok":
            raise sqlite3.DatabaseError("SQLite quick_check failed")

    def reserve(
        self, intent: OrderIntent, snapshot: RiskSnapshot, now: datetime
    ) -> IntentReservation:
        """Validate intent and durably reserve its exposure; duplicate IDs always fail."""
        current = utc(now)
        if not isinstance(intent, OrderIntent) or not isinstance(snapshot, RiskSnapshot):
            raise DomainError("typed OrderIntent and RiskSnapshot are required")
        self._validate_snapshot(intent, snapshot, current)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._check_integrity(connection)
                if self.limits.kill_switch or self._stored_kill_switch(connection):
                    raise DomainError("risk kill switch is active")
                if connection.execute(
                    "SELECT 1 FROM risk_reservations WHERE order_id=?", (intent.order_id.value,)
                ).fetchone():
                    raise DomainError("duplicate order ID is permanently denied")
                active = connection.execute(
                    """SELECT instrument_id, side, quantity, reference_price, reserved_cash,
                    state, filled_quantity, reconciled_quantity FROM risk_reservations"""
                ).fetchall()
                reservation = self._evaluate(intent, snapshot, current, active)
                connection.execute(
                    """INSERT INTO risk_reservations
                    (order_id, state, instrument_id, currency, side, quantity,
                    reference_price, reserved_cash, created_at, updated_at,
                    order_type, limit_price,
                    resolution_source, filled_quantity, reconciled_quantity)
                    VALUES (?, 'reserved', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '0', '0')""",
                    (
                        reservation.order_id,
                        reservation.instrument_id.value,
                        snapshot.currency,
                        reservation.side.value,
                        str(reservation.quantity),
                        str(reservation.reference_price),
                        str(reservation.reserved_cash),
                        reservation.created_at.isoformat(),
                        reservation.updated_at.isoformat(),
                        intent.order_type.value,
                        str(intent.limit_price) if intent.limit_price is not None else None,
                    ),
                )
                connection.commit()
                return reservation
        except DomainError:
            raise
        except sqlite3.DatabaseError as exc:
            raise DomainError(
                "risk reservation failed closed; no authorization was issued"
            ) from exc

    def kill_switch_status(self) -> KillSwitchStatus:
        """Read the effective persisted operator switch and immutable config lock."""
        try:
            with closing(self._connect()) as connection:
                self._check_integrity(connection)
                row = connection.execute("SELECT * FROM risk_control WHERE singleton=1").fetchone()
                if row is None:
                    raise sqlite3.DatabaseError("kill switch record is missing")
                if row["kill_switch_active"] not in (0, 1):
                    raise sqlite3.DatabaseError("kill switch state is invalid")
                return KillSwitchStatus(
                    bool(row["kill_switch_active"]) or self.limits.kill_switch,
                    self.limits.kill_switch,
                    utc(datetime.fromisoformat(row["updated_at"])),
                    row["operator"],
                    row["reason"],
                )
        except sqlite3.DatabaseError as exc:
            raise DomainError("risk kill switch state is unavailable") from exc

    def kill_switch_history(self) -> tuple[KillSwitchStatus, ...]:
        """Return the durable operator history in append order."""
        try:
            with closing(self._connect()) as connection:
                self._check_integrity(connection)
                rows = connection.execute(
                    """SELECT active, changed_at, operator, reason
                    FROM kill_switch_events ORDER BY event_id"""
                ).fetchall()
                if any(row["active"] not in (0, 1) for row in rows):
                    raise sqlite3.DatabaseError("kill switch history contains invalid state")
                return tuple(
                    KillSwitchStatus(
                        bool(row["active"]) or self.limits.kill_switch,
                        self.limits.kill_switch,
                        utc(datetime.fromisoformat(row["changed_at"])),
                        row["operator"],
                        row["reason"],
                    )
                    for row in rows
                )
        except sqlite3.DatabaseError as exc:
            raise DomainError("risk kill switch history is unavailable") from exc

    def set_kill_switch(
        self, active: bool, at: datetime, *, operator: str, reason: str
    ) -> KillSwitchStatus:
        """Persist an operator action; clearing is blocked by immutable config lock."""
        if type(active) is not bool:
            raise DomainError("kill switch active state must be a boolean")
        if not isinstance(operator, str) or not operator.strip() or len(operator) > 128:
            raise DomainError("kill switch operator must be 1-128 characters")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 512:
            raise DomainError("kill switch reason must be 1-512 characters")
        if not active and self.limits.kill_switch:
            raise DomainError("configured kill switch lock cannot be cleared at runtime")
        current = utc(at)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._check_integrity(connection)
                row = connection.execute(
                    "SELECT updated_at FROM risk_control WHERE singleton=1"
                ).fetchone()
                if row is None:
                    raise sqlite3.DatabaseError("kill switch record is missing")
                if current < datetime.fromisoformat(row["updated_at"]):
                    raise DomainError("kill switch timestamps cannot move backwards")
                connection.execute(
                    """UPDATE risk_control SET kill_switch_active=?, updated_at=?,
                    operator=?, reason=? WHERE singleton=1""",
                    (int(active), current.isoformat(), operator.strip(), reason.strip()),
                )
                connection.execute(
                    """INSERT INTO kill_switch_events(active, changed_at, operator, reason)
                    VALUES (?, ?, ?, ?)""",
                    (int(active), current.isoformat(), operator.strip(), reason.strip()),
                )
                connection.commit()
                return KillSwitchStatus(
                    active or self.limits.kill_switch,
                    self.limits.kill_switch,
                    current,
                    operator.strip(),
                    reason.strip(),
                )
        except DomainError:
            raise
        except (sqlite3.DatabaseError, ValueError) as exc:
            raise DomainError("kill switch update failed closed") from exc

    @staticmethod
    def _stored_kill_switch(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT kill_switch_active FROM risk_control WHERE singleton=1"
        ).fetchone()
        if row is None or row["kill_switch_active"] not in (0, 1):
            raise sqlite3.DatabaseError("kill switch record is missing or invalid")
        return bool(row["kill_switch_active"])

    def mark_unknown(self, order_id: str, at: datetime) -> IntentReservation:
        """Keep reservation after a timeout where broker acceptance is ambiguous."""
        return self._set_nonterminal(
            order_id, ReservationState.UNKNOWN, at, {ReservationState.RESERVED}
        )

    def mark_accepted(
        self, order_id: str, at: datetime, source: ResolutionSource
    ) -> IntentReservation:
        if not isinstance(source, ResolutionSource):
            raise DomainError("accepted status requires broker or reconciliation evidence")
        return self._set_nonterminal(
            order_id,
            ReservationState.ACCEPTED,
            at,
            {ReservationState.RESERVED, ReservationState.UNKNOWN},
            source,
        )

    def bind_provider_order(self, order_id: str, mapped_order: object, at: datetime) -> None:
        """Persist one typed provider identity against its exact reserved local LIMIT order."""
        from quant_terminal.execution.brokers.trading212 import MappedProviderOrder

        if type(mapped_order) is not MappedProviderOrder:
            raise DomainError("typed MappedProviderOrder is required")
        if mapped_order.order_id.value != f"t212:{mapped_order.broker_order_id}":
            raise DomainError("provider order identity is not canonically mapped")
        current = utc(at)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._check_integrity(connection)
                row = connection.execute(
                    "SELECT * FROM risk_reservations WHERE order_id=?", (order_id,)
                ).fetchone()
                if row is None:
                    raise DomainError("unknown reservation")
                if (
                    OrderId(order_id) != OrderId(row["order_id"])
                    or row["order_type"] != TradeType.LIMIT.value
                    or row["instrument_id"] != mapped_order.instrument.instrument_id.value
                    or row["side"] != mapped_order.side.value
                    or Decimal(row["quantity"]) != mapped_order.quantity
                    or row["limit_price"] is None
                    or Decimal(row["limit_price"]) != mapped_order.limit_price
                ):
                    raise DomainError("provider order does not match reserved local intent")
                if mapped_order.filled_quantity < Decimal(row["filled_quantity"]):
                    raise DomainError("provider cumulative fills are behind recorded local fills")
                if current < datetime.fromisoformat(row["updated_at"]):
                    raise DomainError("provider order timestamps cannot move backwards")
                existing = connection.execute(
                    "SELECT * FROM risk_provider_bindings WHERE local_order_id=?", (order_id,)
                ).fetchone()
                values = (
                    order_id,
                    mapped_order.broker_order_id,
                    mapped_order.order_id.value,
                    mapped_order.ticker,
                    mapped_order.instrument.instrument_id.value,
                    mapped_order.side.value,
                    str(mapped_order.quantity),
                    str(mapped_order.limit_price),
                    current.isoformat(),
                )
                if existing is not None:
                    if (
                        existing["broker_order_id"] != mapped_order.broker_order_id
                        or existing["provider_order_id"] != mapped_order.order_id.value
                        or existing["ticker"] != mapped_order.ticker
                    ):
                        raise DomainError("local order is already bound to another provider order")
                    connection.commit()
                    return
                connection.execute(
                    """INSERT INTO risk_provider_bindings(
                    local_order_id, broker_order_id, provider_order_id, ticker, instrument_id,
                    side, quantity, limit_price, mapped_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    values,
                )
                connection.commit()
        except DomainError:
            raise
        except sqlite3.IntegrityError as exc:
            raise DomainError("provider order identity is already bound") from exc
        except (sqlite3.DatabaseError, ValueError) as exc:
            raise DomainError("provider order binding failed closed") from exc

    def record_fill(self, order_id: str, aggregate: object, at: datetime) -> Decimal:
        """Record the full typed provider aggregate, never caller-supplied quantities."""
        from quant_terminal.execution.brokers.trading212 import ProviderOrderFillAggregate

        if type(aggregate) is not ProviderOrderFillAggregate or not aggregate.verify_hash():
            raise DomainError("valid typed ProviderOrderFillAggregate is required")
        if aggregate.local_order_id.value != order_id:
            raise DomainError("provider aggregate does not belong to this local reservation")
        current = utc(at)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._check_integrity(connection)
                row = connection.execute(
                    "SELECT * FROM risk_reservations WHERE order_id=?", (order_id,)
                ).fetchone()
                if row is None:
                    raise DomainError("unknown reservation")
                binding = connection.execute(
                    "SELECT * FROM risk_provider_bindings WHERE local_order_id=?", (order_id,)
                ).fetchone()
                mapped = aggregate.mapped_order
                if binding is None:
                    raise DomainError("provider order must be durably bound before recording fills")
                if (
                    binding["broker_order_id"] != aggregate.broker_order_id
                    or binding["provider_order_id"] != mapped.order_id.value
                    or binding["ticker"] != aggregate.ticker
                    or binding["instrument_id"] != aggregate.instrument.instrument_id.value
                    or binding["side"] != aggregate.side.value
                    or Decimal(binding["quantity"]) != aggregate.order_quantity
                    or Decimal(binding["limit_price"]) != mapped.limit_price
                    or aggregate.order_quantity != Decimal(row["quantity"])
                    or aggregate.side.value != row["side"]
                    or aggregate.instrument.instrument_id.value != row["instrument_id"]
                    or mapped.filled_quantity != aggregate.filled_quantity
                ):
                    raise DomainError("provider aggregate mismatches durable local order binding")
                if aggregate.status.value not in (
                    "PARTIALLY_FILLED",
                    "FILLED",
                    "CANCELLED",
                    "REJECTED",
                ):
                    raise DomainError("provider aggregate has unsupported order status")
                if (
                    aggregate.status.value == "FILLED"
                    and aggregate.filled_quantity != aggregate.order_quantity
                ):
                    raise DomainError("provider FILLED status has an incomplete aggregate")
                if aggregate.status.value == "PARTIALLY_FILLED" and not (
                    Decimal("0") < aggregate.filled_quantity < aggregate.order_quantity
                ):
                    raise DomainError("provider partial status has invalid cumulative quantity")
                if (
                    aggregate.status.value in ("CANCELLED", "REJECTED")
                    and aggregate.filled_quantity >= aggregate.order_quantity
                ):
                    raise DomainError("provider terminal status has invalid cumulative quantity")
                if current < datetime.fromisoformat(row["updated_at"]):
                    raise DomainError("fill timestamps cannot move backwards")
                old_fills = {
                    item["fill_id"]: Decimal(item["quantity"])
                    for item in connection.execute(
                        "SELECT fill_id, quantity FROM risk_fills WHERE order_id=?", (order_id,)
                    ).fetchall()
                }
                aggregate_fills = {fill.broker_fill_id: fill.quantity for fill in aggregate.fills}
                if any(
                    aggregate_fills.get(fill_id) != quantity
                    for fill_id, quantity in old_fills.items()
                ):
                    raise DomainError(
                        "provider aggregate omits or changes a previously recorded fill"
                    )
                cumulative = sum(aggregate_fills.values(), Decimal("0"))
                if cumulative != aggregate.filled_quantity:
                    raise DomainError("provider fill identities do not sum to the aggregate total")
                if Decimal(row["filled_quantity"]) > cumulative:
                    raise sqlite3.DatabaseError("fill ledger differs from reservation total")
                if cumulative > Decimal(row["quantity"]):
                    raise DomainError("cumulative fills exceed reserved order quantity")
                for fill in aggregate.fills:
                    if (
                        fill.broker_order_id != aggregate.broker_order_id
                        or fill.ticker != aggregate.ticker
                        or fill.side is not aggregate.side
                    ):
                        raise DomainError("provider fill identity differs from bound order")
                    previous = old_fills.get(fill.broker_fill_id)
                    if previous is not None:
                        continue
                    connection.execute(
                        """INSERT INTO risk_fills(fill_id, order_id, quantity, filled_at)
                        VALUES (?, ?, ?, ?)""",
                        (
                            fill.broker_fill_id,
                            order_id,
                            str(fill.quantity),
                            fill.timestamp.isoformat(),
                        ),
                    )
                if not any(fill.broker_fill_id not in old_fills for fill in aggregate.fills):
                    raise DomainError("duplicate fill identity: aggregate adds no new fills")
                if aggregate.status.value in ("CANCELLED", "REJECTED", "FILLED"):
                    terminal_state = {
                        "CANCELLED": ReservationState.CANCELLED,
                        "REJECTED": ReservationState.REJECTED,
                        "FILLED": ReservationState.FILLED,
                    }[aggregate.status.value]
                    if terminal_state is ReservationState.FILLED and cumulative != Decimal(
                        row["quantity"]
                    ):
                        raise DomainError("full-fill terminal status requires all units")
                    next_state = terminal_state
                else:
                    next_state = ReservationState.ACCEPTED
                connection.execute(
                    """UPDATE risk_reservations SET filled_quantity=?, state=?, updated_at=?,
                    resolution_source='broker' WHERE order_id=?""",
                    (str(cumulative), next_state.value, current.isoformat(), order_id),
                )
                connection.commit()
                return cumulative
        except DomainError:
            raise
        except sqlite3.IntegrityError as exc:
            raise DomainError("duplicate fill identity or invalid fill reference") from exc
        except (sqlite3.DatabaseError, ValueError) as exc:
            raise DomainError("fill record failed closed") from exc

    def reconcile_fills(
        self,
        order_id: str,
        evidence: ReconciliationEvidence,
        local_snapshot: RiskSnapshot,
        at: datetime,
    ) -> IntentReservation:
        """Release filled exposure only against matching immutable broker evidence."""
        if type(evidence) is not ReconciliationEvidence:
            raise DomainError("typed ReconciliationEvidence is required")
        if not isinstance(local_snapshot, RiskSnapshot):
            raise DomainError("typed local RiskSnapshot is required")
        if not evidence.verify_hash() or not evidence.matches:
            raise DomainError("broker reconciliation evidence is mismatched or invalid")
        if evidence.local_snapshot_hash != local_snapshot.content_hash:
            raise DomainError("broker evidence does not match the supplied local snapshot")
        if evidence.currency != local_snapshot.currency:
            raise DomainError("broker evidence currency does not match local state")
        current = utc(at)
        if evidence.observed_at > current:
            raise DomainError("broker reconciliation evidence is from the future")
        if (
            _duration_seconds(current - evidence.observed_at)
            > self.limits.max_market_data_age_seconds
        ):
            raise DomainError("broker reconciliation evidence is stale")
        if (
            _duration_seconds(current - local_snapshot.as_of)
            > self.limits.max_market_data_age_seconds
        ):
            raise DomainError("local positions snapshot is stale")
        aggregate = evidence.provider_aggregates.get(order_id)
        from quant_terminal.execution.brokers.trading212 import ProviderOrderFillAggregate

        if type(aggregate) is not ProviderOrderFillAggregate or not aggregate.verify_hash():
            raise DomainError("broker evidence lacks a valid typed order fill aggregate")
        cumulative_filled_quantity = aggregate.filled_quantity
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._check_integrity(connection)
                row = connection.execute(
                    "SELECT * FROM risk_reservations WHERE order_id=?", (order_id,)
                ).fetchone()
                if row is None:
                    raise DomainError("unknown reservation")
                provider_state = {
                    "PARTIALLY_FILLED": ReservationState.ACCEPTED,
                    "FILLED": ReservationState.FILLED,
                    "CANCELLED": ReservationState.CANCELLED,
                    "REJECTED": ReservationState.REJECTED,
                }.get(aggregate.status.value)
                if (
                    provider_state is None
                    or aggregate.mapped_order.status.provider.value != aggregate.status.value
                    or ReservationState(row["state"]) is not provider_state
                ):
                    raise DomainError("provider order status does not match the bound reservation")
                binding = connection.execute(
                    "SELECT * FROM risk_provider_bindings WHERE local_order_id=?", (order_id,)
                ).fetchone()
                if binding is None:
                    raise DomainError("provider order is not bound to this local reservation")
                if (
                    binding["broker_order_id"] != aggregate.broker_order_id
                    or binding["provider_order_id"] != aggregate.mapped_order.order_id.value
                    or aggregate.local_order_id.value != order_id
                    or binding["ticker"] != aggregate.ticker
                    or binding["instrument_id"] != aggregate.instrument.instrument_id.value
                    or binding["side"] != aggregate.side.value
                    or Decimal(binding["quantity"]) != aggregate.order_quantity
                ):
                    raise DomainError(
                        "provider aggregate does not match durable local order binding"
                    )
                local_fills = {
                    item["fill_id"]: Decimal(item["quantity"])
                    for item in connection.execute(
                        "SELECT fill_id, quantity FROM risk_fills WHERE order_id=?", (order_id,)
                    ).fetchall()
                }
                provider_fills = {fill.broker_fill_id: fill.quantity for fill in aggregate.fills}
                if provider_fills != local_fills:
                    raise DomainError(
                        "provider fill identities do not match locally recorded fills"
                    )
                previous = Decimal(row["reconciled_quantity"])
                filled = Decimal(row["filled_quantity"])
                if cumulative_filled_quantity != filled:
                    raise DomainError(
                        "broker cumulative fills do not match locally recorded unique fills"
                    )
                if cumulative_filled_quantity < previous or cumulative_filled_quantity > filled:
                    raise DomainError(
                        "reconciliation must be monotonic and cannot exceed recorded fills"
                    )
                if current < datetime.fromisoformat(row["updated_at"]):
                    raise DomainError("reconciliation timestamps cannot move backwards")
                reconciliation_id = hashlib.sha256(
                    _canonical_json(
                        {
                            "snapshot_id": evidence.snapshot_id,
                            "snapshot_hash": evidence.snapshot_hash,
                            "order_id": order_id,
                        }
                    )
                ).hexdigest()
                connection.execute(
                    """INSERT INTO risk_reconciliations(
                    reconciliation_id, order_id, cumulative_filled_quantity, reconciled_at, source
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (
                        reconciliation_id,
                        order_id,
                        str(cumulative_filled_quantity),
                        current.isoformat(),
                        ResolutionSource.RECONCILIATION.value,
                    ),
                )
                connection.execute(
                    """UPDATE risk_reservations SET reconciled_quantity=?, updated_at=?
                    WHERE order_id=?""",
                    (str(cumulative_filled_quantity), current.isoformat(), order_id),
                )
                connection.commit()
                return self._row_to_reservation(
                    row,
                    ReservationState(row["state"]),
                    current,
                    reconciled_quantity=cumulative_filled_quantity,
                )
        except DomainError:
            raise
        except sqlite3.IntegrityError as exc:
            raise DomainError("duplicate reconciliation identity") from exc
        except (sqlite3.DatabaseError, ValueError) as exc:
            raise DomainError("fill reconciliation failed closed") from exc

    def resolve_terminal(
        self,
        order_id: str,
        mapped_order: object,
        at: datetime,
    ) -> IntentReservation:
        from quant_terminal.execution.brokers.trading212 import MappedProviderOrder

        if type(mapped_order) is not MappedProviderOrder:
            raise DomainError("typed MappedProviderOrder is required for terminal resolution")
        terminal = {
            "FILLED": ReservationState.FILLED,
            "CANCELLED": ReservationState.CANCELLED,
            "REJECTED": ReservationState.REJECTED,
        }
        state = terminal.get(mapped_order.status.provider.value)
        if state is None:
            raise DomainError("resolution must be a broker-confirmed terminal state")
        current = utc(at)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._check_integrity(connection)
                row = connection.execute(
                    "SELECT * FROM risk_reservations WHERE order_id=?", (order_id,)
                ).fetchone()
                if row is None:
                    raise DomainError("unknown reservation")
                binding = connection.execute(
                    "SELECT * FROM risk_provider_bindings WHERE local_order_id=?", (order_id,)
                ).fetchone()
                if binding is None:
                    raise DomainError("provider order must be bound before terminal resolution")
                if (
                    binding["broker_order_id"] != mapped_order.broker_order_id
                    or binding["provider_order_id"] != mapped_order.order_id.value
                    or binding["ticker"] != mapped_order.ticker
                    or binding["instrument_id"] != mapped_order.instrument.instrument_id.value
                    or binding["side"] != mapped_order.side.value
                    or Decimal(binding["quantity"]) != mapped_order.quantity
                    or Decimal(binding["limit_price"]) != mapped_order.limit_price
                ):
                    raise DomainError("provider terminal order differs from durable binding")
                old = ReservationState(row["state"])
                if old not in (
                    ReservationState.RESERVED,
                    ReservationState.UNKNOWN,
                    ReservationState.ACCEPTED,
                ):
                    raise DomainError("terminal reservation cannot be changed")
                updated = datetime.fromisoformat(row["updated_at"])
                if current < updated:
                    raise DomainError("reservation timestamps cannot move backwards")
                recorded_fills = Decimal(row["filled_quantity"])
                if mapped_order.filled_quantity != recorded_fills:
                    raise DomainError("terminal fill quantity must match recorded unique fills")
                if state is ReservationState.FILLED and recorded_fills != Decimal(row["quantity"]):
                    raise DomainError(
                        "filled terminal status requires all order quantity to be recorded"
                    )
                connection.execute(
                    """UPDATE risk_reservations SET state=?, updated_at=?,
                    resolution_source=? WHERE order_id=?""",
                    (state.value, current.isoformat(), ResolutionSource.BROKER.value, order_id),
                )
                connection.commit()
                return self._row_to_reservation(row, state, current)
        except DomainError:
            raise
        except (sqlite3.DatabaseError, ValueError) as exc:
            raise DomainError("reservation resolution failed closed") from exc

    def _set_nonterminal(
        self,
        order_id: str,
        state: ReservationState,
        at: datetime,
        allowed: set[ReservationState],
        source: ResolutionSource | None = None,
    ) -> IntentReservation:
        if not isinstance(order_id, str) or not order_id:
            raise DomainError("order_id is required")
        current = utc(at)
        try:
            with closing(self._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._check_integrity(connection)
                row = connection.execute(
                    "SELECT * FROM risk_reservations WHERE order_id=?", (order_id,)
                ).fetchone()
                if row is None:
                    raise DomainError("unknown reservation")
                old = ReservationState(row["state"])
                if old not in allowed:
                    raise DomainError("invalid reservation state transition")
                if current < datetime.fromisoformat(row["updated_at"]):
                    raise DomainError("reservation timestamps cannot move backwards")
                connection.execute(
                    """UPDATE risk_reservations SET state=?, updated_at=?,
                    resolution_source=? WHERE order_id=?""",
                    (
                        state.value,
                        current.isoformat(),
                        None if source is None else source.value,
                        order_id,
                    ),
                )
                connection.commit()
                return self._row_to_reservation(row, state, current)
        except DomainError:
            raise
        except (sqlite3.DatabaseError, ValueError) as exc:
            raise DomainError("reservation state update failed closed") from exc

    @staticmethod
    def _row_to_reservation(
        row: sqlite3.Row,
        state: ReservationState,
        updated: datetime,
        *,
        reconciled_quantity: Decimal | None = None,
    ) -> IntentReservation:
        return IntentReservation(
            row["order_id"],
            state,
            InstrumentId(row["instrument_id"]),
            Side(row["side"]),
            Decimal(row["quantity"]),
            Decimal(row["reference_price"]),
            Decimal(row["reserved_cash"]),
            datetime.fromisoformat(row["created_at"]),
            updated,
            Decimal(row["filled_quantity"]),
            Decimal(row["reconciled_quantity"])
            if reconciled_quantity is None
            else reconciled_quantity,
        )

    def get(self, order_id: str) -> IntentReservation | None:
        try:
            with closing(self._connect()) as connection:
                self._check_integrity(connection)
                row = connection.execute(
                    "SELECT * FROM risk_reservations WHERE order_id=?", (order_id,)
                ).fetchone()
                return (
                    None
                    if row is None
                    else self._row_to_reservation(
                        row,
                        ReservationState(row["state"]),
                        datetime.fromisoformat(row["updated_at"]),
                    )
                )
        except (sqlite3.DatabaseError, ValueError) as exc:
            raise DomainError("risk reservation database is unavailable or corrupt") from exc

    def _validate_snapshot(
        self, intent: OrderIntent, snapshot: RiskSnapshot, now: datetime
    ) -> None:
        if (
            snapshot.currency != self.limits.currency
            or intent.instrument.currency != self.limits.currency
        ):
            raise DomainError("unsupported currency; FX risk is not implemented")
        if intent.instrument.instrument_id not in self.limits.allowed_instruments:
            raise DomainError("instrument is not enabled by risk policy")
        if intent.order_type not in self.limits.allowed_trade_types:
            raise DomainError("trade type is not supported by risk policy")
        if not self.limits.allow_short and intent.side is Side.SELL:
            current = snapshot.positions.get(intent.instrument.instrument_id, Decimal("0"))
            if intent.quantity > current:
                raise DomainError("long-only policy blocks short position")
        if snapshot.as_of > now or intent.created_at > now:
            raise DomainError("future risk or intent timestamp")
        for timestamp in snapshot.market_price_times.values():
            if timestamp > now:
                raise DomainError("future market-data timestamp")
        for instrument_id, quantity in snapshot.positions.items():
            if quantity != 0:
                observed = snapshot.market_price_times.get(instrument_id)
                if (
                    observed is None
                    or _duration_seconds(now - observed) > self.limits.max_market_data_age_seconds
                ):
                    raise DomainError("position mark is stale or missing")
        for instrument_id, quantity in snapshot.positions.items():
            if quantity != 0 and instrument_id not in snapshot.market_prices:
                raise DomainError("position has no current market price")
        for instrument_id in snapshot.positions:
            if instrument_id not in self.limits.allowed_instruments:
                raise DomainError("snapshot contains an unsupported position instrument")
        iid = intent.instrument.instrument_id
        if iid not in snapshot.market_prices or iid not in snapshot.market_price_times:
            raise DomainError("current market price and timestamp are required")
        age = _duration_seconds(now - snapshot.market_price_times[iid])
        if age < 0 or age > self.limits.max_market_data_age_seconds:
            raise DomainError("market data is stale")
        if snapshot.daily_pnl <= -self.limits.max_daily_loss:
            raise DomainError("daily loss limit breached")
        if (
            snapshot.peak_equity - snapshot.equity
        ) / snapshot.peak_equity >= self.limits.max_drawdown:
            raise DomainError("drawdown limit breached")

    def _evaluate(
        self, intent: OrderIntent, snapshot: RiskSnapshot, now: datetime, active: list[sqlite3.Row]
    ) -> IntentReservation:
        iid = intent.instrument.instrument_id
        mark = snapshot.market_prices[iid]
        if intent.order_type in (TradeType.LIMIT, TradeType.STOP_LIMIT):
            assert intent.limit_price is not None
            price = intent.limit_price
        elif intent.order_type is TradeType.STOP:
            if intent.side is Side.BUY:
                if intent.max_execution_price is None:
                    raise DomainError("stop buys require a maximum execution price")
                price = intent.max_execution_price
            else:
                assert intent.stop_price is not None
                price = intent.stop_price
        elif intent.side is Side.BUY:
            if intent.max_execution_price is None:
                raise DomainError("market and stop buys require a maximum execution price")
            price = intent.max_execution_price
        else:
            price = mark
        order_value = intent.quantity * price
        if order_value > self.limits.max_order_value:
            raise DomainError("maximum order value exceeded")

        position_values = {
            key: abs(quantity * snapshot.market_prices[key])
            for key, quantity in snapshot.positions.items()
            if quantity != 0
        }
        reserved_by_instrument: dict[InstrumentId, Decimal] = {}
        for row in active:
            key = InstrumentId(row["instrument_id"])
            obligation_quantity = self._obligation_quantity(row)
            pending_value = obligation_quantity * Decimal(row["reference_price"])
            reserved_by_instrument[key] = (
                reserved_by_instrument.get(key, Decimal("0")) + pending_value
            )
        candidate_value = intent.quantity * price

        if not self.limits.allow_short:
            for key, quantity in snapshot.positions.items():
                pending_sells = sum(
                    (
                        Decimal(row["quantity"])
                        for row in active
                        if row["instrument_id"] == key.value
                        and row["side"] == Side.SELL.value
                        and self._obligation_quantity(row) > 0
                    ),
                    Decimal("0"),
                )
                if quantity < 0 or pending_sells > quantity:
                    raise DomainError("long-only policy blocks short position")
            if intent.side is Side.SELL:
                held = snapshot.positions.get(iid, Decimal("0"))
                already_selling = sum(
                    (
                        Decimal(row["quantity"])
                        for row in active
                        if row["instrument_id"] == iid.value
                        and row["side"] == Side.SELL.value
                        and self._obligation_quantity(row) > 0
                    ),
                    Decimal("0"),
                )
                if already_selling + intent.quantity > held:
                    raise DomainError("long-only policy blocks short position")

        for key, current_value in position_values.items():
            gross_for_instrument = current_value + reserved_by_instrument.get(key, Decimal("0"))
            if key == iid:
                gross_for_instrument += candidate_value
            if gross_for_instrument > self.limits.max_position_value:
                raise DomainError("maximum position value exceeded")
        for key, reserved_value in reserved_by_instrument.items():
            if key not in position_values:
                gross_for_instrument = reserved_value + (
                    candidate_value if key == iid else Decimal("0")
                )
                if gross_for_instrument > self.limits.max_position_value:
                    raise DomainError("maximum position value exceeded")
        if iid not in position_values and iid not in reserved_by_instrument:
            if candidate_value > self.limits.max_position_value:
                raise DomainError("maximum position value exceeded")

        current_net = sum(
            (
                quantity * snapshot.market_prices[key]
                for key, quantity in snapshot.positions.items()
            ),
            Decimal("0"),
        )
        pending_order_value = sum(reserved_by_instrument.values(), Decimal("0")) + candidate_value
        gross = sum(position_values.values(), Decimal("0")) + pending_order_value
        net_bound = abs(current_net) + pending_order_value
        if gross > self.limits.max_gross_exposure:
            raise DomainError("maximum gross exposure exceeded")
        if net_bound > self.limits.max_net_exposure:
            raise DomainError("maximum net exposure exceeded")
        if gross / snapshot.equity > self.limits.max_leverage:
            raise DomainError("maximum leverage exceeded")

        reserved_cash = order_value if intent.side is Side.BUY else Decimal("0")
        prior_cash = sum(
            (
                self._obligation_quantity(row) * Decimal(row["reference_price"])
                for row in active
                if row["side"] == Side.BUY.value
            ),
            Decimal("0"),
        )
        if (
            prior_cash + reserved_cash > self.limits.max_cash_reservation
            or prior_cash + reserved_cash > snapshot.cash
        ):
            raise DomainError("cash reservation limit exceeded")
        return IntentReservation(
            intent.order_id.value,
            ReservationState.RESERVED,
            iid,
            intent.side,
            intent.quantity,
            price,
            reserved_cash,
            now,
            now,
        )

    @staticmethod
    def _obligation_quantity(row: sqlite3.Row) -> Decimal:
        state = ReservationState(row["state"])
        filled = Decimal(row["filled_quantity"])
        reconciled = Decimal(row["reconciled_quantity"])
        quantity = Decimal(row["quantity"])
        if reconciled > filled or filled > quantity:
            raise sqlite3.DatabaseError("reservation fill quantities are inconsistent")
        if state in (
            ReservationState.RESERVED,
            ReservationState.UNKNOWN,
            ReservationState.ACCEPTED,
        ):
            return quantity - reconciled
        return filled - reconciled
