from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Barrier

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId, OrderId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.execution.brokers.trading212 import (
    BrokerReconciliation,
    DemoCredentials,
    MappedProviderOrder,
    PositionReconciliation,
    Trading212Adapter,
)
from quant_terminal.orders.models import OrderIntent, Side, TradeType
from quant_terminal.risk.pretrade import (
    PreTradeRisk,
    ReconciliationEvidence,
    ReservationState,
    RiskLimits,
    RiskSnapshot,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
IID = InstrumentId("SPY")
META = InstrumentMeta(
    IID, "SPY", "USD", "etf", "XNYS", "America/New_York", Decimal("0.01"), Decimal("1")
)


def limits(**overrides: object) -> RiskLimits:
    values: dict[str, object] = {
        "currency": "USD",
        "allowed_instruments": frozenset({IID}),
        "max_position_value": Decimal("5000"),
        "max_order_value": Decimal("3000"),
        "max_gross_exposure": Decimal("8000"),
        "max_net_exposure": Decimal("8000"),
        "max_leverage": Decimal("2"),
        "max_daily_loss": Decimal("1000"),
        "max_drawdown": Decimal("0.25"),
        "max_market_data_age_seconds": Decimal("30"),
        "max_cash_reservation": Decimal("5000"),
    }
    values.update(overrides)
    return RiskLimits(**values)  # type: ignore[arg-type]


def snapshot(
    *,
    positions: dict[InstrumentId, Decimal] | None = None,
    equity: Decimal = Decimal("10000"),
    cash: Decimal = Decimal("10000"),
    daily_pnl: Decimal = Decimal("0"),
    peak_equity: Decimal = Decimal("10000"),
    price: Decimal = Decimal("100"),
    quote_time: datetime | None = None,
    as_of: datetime | None = None,
    currency: str = "USD",
) -> RiskSnapshot:
    return RiskSnapshot(
        as_of=as_of or NOW,
        currency=currency,
        equity=equity,
        cash=cash,
        daily_pnl=daily_pnl,
        peak_equity=peak_equity,
        positions=positions or {},
        market_prices={IID: price},
        market_price_times={IID: quote_time or NOW},
    )


def intent(
    name: str = "order-1",
    *,
    side: Side = Side.BUY,
    quantity: Decimal = Decimal("10"),
    order_type: TradeType = TradeType.LIMIT,
    price: Decimal | None = Decimal("100"),
    stop_price: Decimal | None = None,
    created_at: datetime = NOW,
) -> OrderIntent:
    return OrderIntent(
        OrderId(name),
        META,
        side,
        order_type,
        quantity,
        created_at,
        limit_price=price if order_type is TradeType.LIMIT else None,
        stop_price=stop_price if order_type in (TradeType.STOP, TradeType.STOP_LIMIT) else None,
        max_execution_price=price if order_type is TradeType.MARKET and side is Side.BUY else None,
    )


def provider_order(
    local_order: OrderIntent,
    *,
    filled: Decimal = Decimal("0"),
    status: str = "NEW",
) -> MappedProviderOrder:
    adapter = Trading212Adapter(
        DemoCredentials("synthetic", "synthetic"), {"SPY_US_EQ": META}, "USD"
    )
    quantity = local_order.quantity
    return adapter.parse_order(
        {
            "id": 1234,
            "ticker": "SPY_US_EQ",
            "currency": "USD",
            "side": local_order.side.value.upper(),
            "status": status,
            "quantity": float(quantity),
            "filledQuantity": float(filled),
            "createdAt": NOW.isoformat(),
            "type": "LIMIT",
            "limitPrice": float(local_order.limit_price or Decimal("100")),
        }
    )


def provider_aggregate(
    local_order: OrderIntent,
    *,
    filled: Decimal,
    fill_ids: tuple[str, ...] = ("5678",),
    status: str = "PARTIALLY_FILLED",
    broker_order_id: str = "1234",
):
    adapter = Trading212Adapter(
        DemoCredentials("synthetic", "synthetic"), {"SPY_US_EQ": META}, "USD"
    )
    rows = [
        {
            "order": {
                "id": int(broker_order_id),
                "ticker": "SPY_US_EQ",
                "currency": "USD",
                "side": local_order.side.value.upper(),
                "status": status,
                "quantity": float(local_order.quantity),
                "filledQuantity": float(filled),
                "createdAt": NOW.isoformat(),
                "type": "LIMIT",
                "limitPrice": float(local_order.limit_price or Decimal("100")),
            },
            "fill": {
                "id": int(fill_id),
                "filledAt": NOW.isoformat(),
                "price": 100.0,
                "quantity": float(filled / Decimal(len(fill_ids))),
                "type": "TRADE",
            },
        }
        for fill_id in fill_ids
    ]
    aggregate = adapter.aggregate_historical_order_fills(
        rows,
        expected_broker_order_id=broker_order_id,
        expected_local_order=local_order,
    )
    return provider_order(local_order, filled=filled, status=status), aggregate


def bind(gate: PreTradeRisk, local: OrderIntent, *, broker_id: str = "1234") -> MappedProviderOrder:
    mapped = provider_order(local)
    if broker_id != "1234":
        from dataclasses import replace

        mapped = replace(mapped, broker_order_id=broker_id, order_id=OrderId(f"t212:{broker_id}"))
    gate.bind_provider_order(local.order_id.value, mapped, NOW)
    return mapped


def register_fills(
    gate: PreTradeRisk,
    local: OrderIntent,
    quantity: Decimal,
    *,
    status: str = "PARTIALLY_FILLED",
) -> tuple[MappedProviderOrder, object]:
    mapped, aggregate = provider_aggregate(local, filled=quantity, status=status)
    gate.bind_provider_order(local.order_id.value, mapped, NOW)
    assert gate.record_fill(local.order_id.value, aggregate, NOW) == quantity
    return mapped, aggregate


def test_exact_pretrade_approval_and_durable_reservation(tmp_path: Path) -> None:
    db = tmp_path / "risk.sqlite3"
    approved = PreTradeRisk(db, limits()).reserve(intent(), snapshot(), NOW)
    assert approved.state is ReservationState.RESERVED
    assert approved.reference_price == Decimal("100")
    assert approved.reserved_cash == Decimal("1000")
    assert PreTradeRisk(db, limits()).get("order-1") == approved


def test_duplicate_order_id_denied_after_restart_even_when_terminal(tmp_path: Path) -> None:
    db = tmp_path / "risk.sqlite3"
    gate = PreTradeRisk(db, limits())
    gate.reserve(intent(), snapshot(), NOW)
    mapped = provider_order(intent(), status="REJECTED")
    gate.bind_provider_order("order-1", mapped, NOW)
    gate.resolve_terminal("order-1", mapped, NOW)
    with pytest.raises(DomainError, match="duplicate order ID"):
        PreTradeRisk(db, limits()).reserve(intent(), snapshot(), NOW)


def test_ambiguous_timeout_remains_reserved_across_restart(tmp_path: Path) -> None:
    db = tmp_path / "risk.sqlite3"
    gate = PreTradeRisk(db, limits())
    gate.reserve(intent(), snapshot(), NOW)
    gate.mark_unknown("order-1", NOW + timedelta(seconds=1))
    restarted = PreTradeRisk(db, limits())
    assert restarted.get("order-1").state is ReservationState.UNKNOWN  # type: ignore[union-attr]
    cash_limited = PreTradeRisk(db, limits(max_cash_reservation=Decimal("3000")))
    with pytest.raises(DomainError, match="cash reservation"):
        cash_limited.reserve(
            intent("order-2", quantity=Decimal("21")), snapshot(), NOW + timedelta(seconds=2)
        )


def test_terminal_resolution_requires_confirmed_source_and_terminal_state(tmp_path: Path) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits())
    gate.reserve(intent(), snapshot(), NOW)
    mapped = provider_order(intent(), status="NEW")
    gate.bind_provider_order("order-1", mapped, NOW)
    with pytest.raises(DomainError, match="terminal state"):
        gate.resolve_terminal("order-1", mapped, NOW)
    with pytest.raises(DomainError, match="resolution"):
        gate.resolve_terminal("order-1", "provider-status", NOW)  # type: ignore[arg-type]
    mapped = provider_order(intent(), status="CANCELLED")
    terminal = gate.resolve_terminal(
        "order-1",
        mapped,
        NOW,
    )
    assert terminal.state is ReservationState.CANCELLED


@pytest.mark.parametrize(
    ("risk_limits", "account", "order", "message"),
    [
        ({"max_order_value": Decimal("999")}, {}, {}, "maximum order value"),
        ({"max_position_value": Decimal("999")}, {}, {}, "maximum position value"),
        ({"max_gross_exposure": Decimal("999")}, {}, {}, "maximum gross exposure"),
        ({"max_net_exposure": Decimal("999")}, {}, {}, "maximum net exposure"),
        ({"max_leverage": Decimal("0.05")}, {}, {}, "maximum leverage"),
        ({}, {"daily_pnl": Decimal("-1000")}, {}, "daily loss"),
        ({}, {"equity": Decimal("7000"), "peak_equity": Decimal("10000")}, {}, "drawdown"),
        ({}, {}, {"quote_time": NOW - timedelta(seconds=31)}, "stale"),
        ({"kill_switch": True}, {}, {}, "kill switch"),
    ],
)
def test_risk_boundaries_reject(
    tmp_path: Path,
    risk_limits: dict[str, object],
    account: dict[str, object],
    order: dict[str, object],
    message: str,
) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits(**risk_limits))
    with pytest.raises(DomainError, match=message):
        gate.reserve(intent(), snapshot(**(order or account)), NOW)


def test_operator_kill_switch_persists_history_and_blocks_restart_admission(
    tmp_path: Path,
) -> None:
    db = tmp_path / "risk.sqlite3"
    gate = PreTradeRisk(db, limits())
    active = gate.set_kill_switch(True, NOW, operator="operator-1", reason="manual stop")
    assert active.active is True
    restarted = PreTradeRisk(db, limits())
    assert restarted.kill_switch_status().active is True
    assert restarted.kill_switch_history() == (active,)

    def submit(index: int) -> str:
        concurrent = PreTradeRisk(db, limits())
        try:
            concurrent.reserve(intent(f"blocked-{index}"), snapshot(), NOW + timedelta(seconds=1))
            return "approved"
        except DomainError as exc:
            return "blocked" if "kill switch" in str(exc) else "unexpected rejection"

    with ThreadPoolExecutor(max_workers=5) as executor:
        outcomes = list(executor.map(submit, range(5)))
    assert outcomes == ["blocked"] * 5

    cleared = restarted.set_kill_switch(
        False, NOW + timedelta(seconds=2), operator="operator-1", reason="review complete"
    )
    assert cleared.active is False
    assert len(PreTradeRisk(db, limits()).kill_switch_history()) == 2


def test_partial_cancel_keeps_unreconciled_fills_reserved_until_position_snapshot(
    tmp_path: Path,
) -> None:
    db = tmp_path / "risk.sqlite3"
    risk_limits = limits(max_cash_reservation=Decimal("1500"))
    gate = PreTradeRisk(db, risk_limits)
    local = intent(quantity=Decimal("10"))
    gate.reserve(local, snapshot(), NOW)
    mapped, aggregate = register_fills(gate, local, Decimal("4"))
    mapped, aggregate = provider_aggregate(local, filled=Decimal("4"), status="CANCELLED")
    cancelled = gate.resolve_terminal("order-1", mapped, NOW + timedelta(seconds=1))
    assert cancelled.outstanding_quantity == Decimal("4")
    restarted = PreTradeRisk(db, risk_limits)
    assert restarted.get("order-1").outstanding_quantity == Decimal("4")  # type: ignore[union-attr]
    with pytest.raises(DomainError, match="cash reservation"):
        restarted.reserve(
            intent("follow-up", quantity=Decimal("12")),
            snapshot(),
            NOW + timedelta(seconds=2),
        )

    updated_snapshot = snapshot(
        positions={IID: Decimal("4")},
        cash=Decimal("9600"),
        as_of=NOW + timedelta(seconds=2),
    )
    evidence = reconciliation_evidence(
        updated_snapshot,
        provider_aggregates=(aggregate,),
        observed_at=NOW + timedelta(seconds=2),
    )
    reconciled = restarted.reconcile_fills(
        "order-1", evidence, updated_snapshot, NOW + timedelta(seconds=2)
    )
    assert reconciled.outstanding_quantity == Decimal("0")
    approved = restarted.reserve(
        intent("follow-up", quantity=Decimal("12")),
        updated_snapshot,
        NOW + timedelta(seconds=2),
    )
    assert approved.state is ReservationState.RESERVED


def test_full_fill_remains_reserved_until_reconciled_snapshot_and_duplicate_ids_fail(
    tmp_path: Path,
) -> None:
    db = tmp_path / "risk.sqlite3"
    risk_limits = limits(max_cash_reservation=Decimal("1500"))
    gate = PreTradeRisk(db, risk_limits)
    local = intent(quantity=Decimal("10"))
    gate.reserve(local, snapshot(), NOW)
    mapped, aggregate = register_fills(gate, local, Decimal("10"), status="FILLED")
    terminal = gate.get("order-1")
    assert terminal.outstanding_quantity == Decimal("10")
    restarted = PreTradeRisk(db, risk_limits)
    assert restarted.get("order-1").outstanding_quantity == Decimal("10")  # type: ignore[union-attr]
    with pytest.raises(DomainError, match="duplicate fill identity"):
        restarted.record_fill("order-1", aggregate, NOW + timedelta(seconds=2))
    with pytest.raises(DomainError, match="cash reservation"):
        restarted.reserve(
            intent("follow-up", quantity=Decimal("6")), snapshot(), NOW + timedelta(seconds=2)
        )

    updated_snapshot = snapshot(
        positions={IID: Decimal("10")},
        cash=Decimal("9000"),
        as_of=NOW + timedelta(seconds=2),
    )
    evidence = reconciliation_evidence(
        updated_snapshot,
        provider_aggregates=(aggregate,),
        observed_at=NOW + timedelta(seconds=2),
    )
    reconciled = restarted.reconcile_fills(
        "order-1", evidence, updated_snapshot, NOW + timedelta(seconds=2)
    )
    assert reconciled.outstanding_quantity == Decimal("0")
    with pytest.raises(DomainError, match="duplicate reconciliation identity"):
        restarted.reconcile_fills("order-1", evidence, updated_snapshot, NOW + timedelta(seconds=3))
    approved = restarted.reserve(
        intent("follow-up", quantity=Decimal("6")),
        updated_snapshot,
        NOW + timedelta(seconds=3),
    )
    assert approved.state is ReservationState.RESERVED


def test_fill_quantity_must_match_terminal_broker_quantity(tmp_path: Path) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits())
    local = intent()
    gate.reserve(local, snapshot(), NOW)
    mapped, _aggregate = register_fills(gate, local, Decimal("2"))
    mapped = provider_order(local, filled=Decimal("3"), status="CANCELLED")
    with pytest.raises(DomainError, match="match recorded unique fills"):
        gate.resolve_terminal("order-1", mapped, NOW + timedelta(seconds=1))
    assert gate.get("order-1").state is ReservationState.ACCEPTED  # type: ignore[union-attr]


def test_mismatched_broker_positions_cannot_release_filled_reservation(tmp_path: Path) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits())
    local = intent(quantity=Decimal("10"))
    gate.reserve(local, snapshot(), NOW)
    mapped, aggregate = register_fills(gate, local, Decimal("4"))
    cancelled, aggregate = provider_aggregate(local, filled=Decimal("4"), status="CANCELLED")
    gate.resolve_terminal("order-1", cancelled, NOW + timedelta(seconds=1))
    view = snapshot(
        positions={IID: Decimal("4")},
        cash=Decimal("9600"),
        as_of=NOW + timedelta(seconds=2),
    )
    mismatch = reconciliation_evidence(
        view,
        broker_positions={IID: Decimal("3")},
        provider_aggregates=(aggregate,),
        observed_at=NOW + timedelta(seconds=2),
    )
    assert mismatch.matches is False
    with pytest.raises(DomainError, match="mismatched or invalid"):
        gate.reconcile_fills("order-1", mismatch, view, NOW + timedelta(seconds=2))
    assert gate.get("order-1").outstanding_quantity == Decimal("4")  # type: ignore[union-attr]


def test_reconciliation_evidence_is_snapshot_bound_and_rejects_arbitrary_identity(
    tmp_path: Path,
) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits())
    local = intent(quantity=Decimal("10"))
    gate.reserve(local, snapshot(), NOW)
    mapped, aggregate = register_fills(gate, local, Decimal("4"))
    cancelled, aggregate = provider_aggregate(local, filled=Decimal("4"), status="CANCELLED")
    gate.resolve_terminal("order-1", cancelled, NOW + timedelta(seconds=1))
    view = snapshot(
        positions={IID: Decimal("4")},
        cash=Decimal("9600"),
        as_of=NOW + timedelta(seconds=2),
    )
    broker_result = reconciliation(view)
    with pytest.raises(DomainError, match="snapshot ID must match"):
        ReconciliationEvidence.from_broker_reconciliation(
            view,
            broker_result,
            snapshot_id="arbitrary-id",
            ticker_to_instrument={IID.value: IID},
            provider_aggregates=(aggregate,),
            observed_at=NOW + timedelta(seconds=2),
        )
    evidence = ReconciliationEvidence.from_broker_reconciliation(
        view,
        broker_result,
        snapshot_id=broker_result.broker_snapshot_id,
        ticker_to_instrument={IID.value: IID},
        provider_aggregates=(aggregate,),
        observed_at=NOW + timedelta(seconds=2),
    )
    changed_view = snapshot(
        positions={IID: Decimal("4")},
        cash=Decimal("9500"),
        as_of=NOW + timedelta(seconds=2),
    )
    with pytest.raises(DomainError, match="does not match the supplied local snapshot"):
        gate.reconcile_fills("order-1", evidence, changed_view, NOW + timedelta(seconds=2))
    with pytest.raises(DomainError, match="typed ReconciliationEvidence"):
        gate.reconcile_fills("order-1", "matched", view, NOW + timedelta(seconds=2))  # type: ignore[arg-type]
    assert gate.get("order-1").outstanding_quantity == Decimal("4")  # type: ignore[union-attr]


def test_reconciliation_requires_matching_cumulative_fill_and_fresh_evidence(
    tmp_path: Path,
) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits())
    local = intent(quantity=Decimal("10"))
    gate.reserve(local, snapshot(), NOW)
    mapped, aggregate = register_fills(gate, local, Decimal("4"))
    view = snapshot(
        positions={IID: Decimal("4")},
        cash=Decimal("9600"),
        as_of=NOW + timedelta(seconds=2),
    )
    wrong_quantity = reconciliation_evidence(
        view,
        provider_aggregates=(),
        observed_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(DomainError, match="lacks a valid typed order fill aggregate"):
        gate.reconcile_fills("order-1", wrong_quantity, view, NOW + timedelta(seconds=2))

    future = reconciliation_evidence(
        view,
        provider_aggregates=(aggregate,),
        observed_at=NOW + timedelta(seconds=3),
    )
    with pytest.raises(DomainError, match="from the future"):
        gate.reconcile_fills("order-1", future, view, NOW + timedelta(seconds=2))
    stale = reconciliation_evidence(
        view,
        provider_aggregates=(aggregate,),
        observed_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(DomainError, match="is stale"):
        gate.reconcile_fills("order-1", stale, view, NOW + timedelta(seconds=40))
    assert gate.get("order-1").outstanding_quantity == Decimal("10")  # type: ignore[union-attr]


def test_reconciliation_rejects_unknown_provider_fill_identity(tmp_path: Path) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits())
    local = intent(quantity=Decimal("10"))
    gate.reserve(local, snapshot(), NOW)
    _mapped, _recorded = register_fills(gate, local, Decimal("4"))
    _other_mapped, unknown_fill_aggregate = provider_aggregate(
        local, filled=Decimal("4"), fill_ids=("9999",)
    )
    view = snapshot(
        positions={IID: Decimal("4")},
        cash=Decimal("9600"),
        as_of=NOW + timedelta(seconds=2),
    )
    evidence = reconciliation_evidence(
        view,
        provider_aggregates=(unknown_fill_aggregate,),
        observed_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(DomainError, match="fill identities do not match"):
        gate.reconcile_fills("order-1", evidence, view, NOW + timedelta(seconds=2))
    assert gate.get("order-1").outstanding_quantity == Decimal("10")  # type: ignore[union-attr]


def test_reconciliation_provider_status_must_match_local_terminal_state(tmp_path: Path) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits())
    local = intent(quantity=Decimal("10"))
    gate.reserve(local, snapshot(), NOW)
    _mapped, _recorded = register_fills(gate, local, Decimal("4"))
    _cancelled_mapped, cancelled_aggregate = provider_aggregate(
        local, filled=Decimal("4"), status="CANCELLED"
    )
    view = snapshot(
        positions={IID: Decimal("4")},
        cash=Decimal("9600"),
        as_of=NOW + timedelta(seconds=2),
    )
    evidence = reconciliation_evidence(
        view,
        provider_aggregates=(cancelled_aggregate,),
        observed_at=NOW + timedelta(seconds=2),
    )
    with pytest.raises(DomainError, match="status does not match"):
        gate.reconcile_fills("order-1", evidence, view, NOW + timedelta(seconds=2))


def test_configured_kill_switch_cannot_be_cleared_by_operator(tmp_path: Path) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits(kill_switch=True))
    with pytest.raises(DomainError, match="configured kill switch lock"):
        gate.set_kill_switch(False, NOW, operator="operator-1", reason="attempt clear")


def test_long_only_and_unsupported_currency_or_type_fail_closed(tmp_path: Path) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits())
    with pytest.raises(DomainError, match="long-only"):
        gate.reserve(intent(side=Side.SELL), snapshot(), NOW)
    with pytest.raises(DomainError, match="currency"):
        gate.reserve(intent(), snapshot(currency="EUR"), NOW)
    restrictive = PreTradeRisk(
        tmp_path / "restricted.sqlite3", limits(allowed_trade_types=frozenset({TradeType.LIMIT}))
    )
    with pytest.raises(DomainError, match="trade type"):
        restrictive.reserve(
            intent(order_type=TradeType.STOP, stop_price=Decimal("101")), snapshot(), NOW
        )


@pytest.mark.parametrize("field", ["as_of", "quote_time", "created_at"])
def test_future_timestamps_are_rejected(tmp_path: Path, field: str) -> None:
    gate = PreTradeRisk(tmp_path / "risk.sqlite3", limits())
    kwargs = {field: NOW + timedelta(seconds=1)}
    with pytest.raises(DomainError, match="(future|after the risk snapshot)"):
        gate.reserve(
            intent(**kwargs) if field == "created_at" else intent(),
            snapshot(**kwargs) if field != "created_at" else snapshot(),
            NOW,
        )


def test_position_loss_and_stale_position_mark_fail_closed(tmp_path: Path) -> None:
    other = InstrumentId("QQQ")
    gate = PreTradeRisk(
        tmp_path / "risk.sqlite3", limits(allowed_instruments=frozenset({IID, other}))
    )
    with pytest.raises(DomainError, match="mark is stale"):
        gate.reserve(intent(), snapshot(positions={other: Decimal("2")}), NOW)


def test_stale_mark_for_existing_position_is_rejected(tmp_path: Path) -> None:
    other = InstrumentId("QQQ")
    gate = PreTradeRisk(
        tmp_path / "risk.sqlite3", limits(allowed_instruments=frozenset({IID, other}))
    )
    view = RiskSnapshot(
        NOW,
        "USD",
        Decimal("10000"),
        Decimal("10000"),
        Decimal("0"),
        Decimal("10000"),
        {other: Decimal("2")},
        {IID: Decimal("100"), other: Decimal("50")},
        {IID: NOW, other: NOW - timedelta(seconds=31)},
    )
    with pytest.raises(DomainError, match="position mark is stale"):
        gate.reserve(intent(), view, NOW)


def test_negative_cash_or_future_quote_after_snapshot_are_invalid() -> None:
    with pytest.raises(DomainError, match="cash cannot be negative"):
        snapshot(cash=Decimal("-1"))
    with pytest.raises(DomainError, match="after the risk snapshot"):
        snapshot(as_of=NOW - timedelta(seconds=1), quote_time=NOW)


def reconciliation(
    view: RiskSnapshot,
    *,
    broker_cash: Decimal | None = None,
    broker_positions: dict[InstrumentId, Decimal] | None = None,
    observed_at: datetime | None = None,
) -> BrokerReconciliation:
    broker_cash = view.cash if broker_cash is None else broker_cash
    broker_positions = view.positions if broker_positions is None else broker_positions
    position_rows = tuple(
        PositionReconciliation(
            instrument_id.value,
            view.positions.get(instrument_id, Decimal("0")),
            broker_positions.get(instrument_id, Decimal("0")),
            view.positions.get(instrument_id, Decimal("0"))
            == broker_positions.get(instrument_id, Decimal("0")),
        )
        for instrument_id in sorted(
            set(view.positions) | set(broker_positions), key=lambda item: item.value
        )
    )
    cash_matches = view.cash == broker_cash
    matches = cash_matches and all(row.matches for row in position_rows)
    identity_material = (
        f"{observed_at or view.as_of}:{view.currency}:{broker_cash}:{broker_positions}"
    )
    snapshot_id = "sha256:" + hashlib.sha256(identity_material.encode()).hexdigest()
    return BrokerReconciliation(
        currency=view.currency,
        broker_snapshot_id=snapshot_id,
        local_available_cash=view.cash,
        broker_available_cash=broker_cash,
        cash_matches=cash_matches,
        positions=position_rows,
        matches=matches,
    )


def reconciliation_evidence(
    view: RiskSnapshot,
    *,
    broker_positions: dict[InstrumentId, Decimal] | None = None,
    broker_cash: Decimal | None = None,
    provider_aggregates: tuple[object, ...] = (),
    observed_at: datetime | None = None,
    snapshot_id: str | None = None,
) -> ReconciliationEvidence:
    result = reconciliation(
        view,
        broker_cash=broker_cash,
        broker_positions=broker_positions,
        observed_at=observed_at,
    )
    return ReconciliationEvidence.from_broker_reconciliation(
        view,
        result,
        snapshot_id=result.broker_snapshot_id if snapshot_id is None else snapshot_id,
        ticker_to_instrument={row.ticker: IID for row in result.positions},
        provider_aggregates=provider_aggregates,
        observed_at=observed_at or view.as_of,
    )


def test_concurrent_reservations_cannot_overbook_cash(tmp_path: Path) -> None:
    db = tmp_path / "risk.sqlite3"
    risk_limits = limits(max_order_value=Decimal("1000"), max_cash_reservation=Decimal("1000"))
    snapshot_value = snapshot(cash=Decimal("1000"))

    def submit(index: int) -> str:
        gate = PreTradeRisk(db, risk_limits)
        try:
            gate.reserve(intent(f"parallel-{index}"), snapshot_value, NOW)
            return "approved"
        except DomainError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(submit, range(8)))
    assert outcomes.count("approved") == 1
    assert outcomes.count("rejected") == 7


def test_concurrent_schema_initialization_serializes_migrations(tmp_path: Path) -> None:
    worker_count = 12
    for attempt in range(4):
        database = tmp_path / f"parallel-init-{attempt}.sqlite3"
        barrier = Barrier(worker_count)

        def initialize(
            _index: int, *, sync: Barrier = barrier, db_path: Path = database
        ) -> PreTradeRisk:
            sync.wait(timeout=10)
            return PreTradeRisk(db_path, limits())

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            instances = list(executor.map(initialize, range(worker_count)))
        assert len(instances) == worker_count
        assert all(instance.get("missing") is None for instance in instances)


def test_corrupt_database_fails_closed(tmp_path: Path) -> None:
    db = tmp_path / "risk.sqlite3"
    db.write_bytes(b"not a sqlite database")
    with pytest.raises(DomainError, match="unavailable or corrupt"):
        PreTradeRisk(db, limits())
