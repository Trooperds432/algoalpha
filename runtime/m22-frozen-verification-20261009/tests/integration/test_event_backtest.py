"""Hand-calculated complete backtest and point-in-time strategy fixtures."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quant_terminal.backtesting.engine import BacktestEngine
from quant_terminal.backtesting.models import BacktestConfig
from quant_terminal.backtesting.strategies import (
    BuyAndHold,
    MovingAverageCross,
    SimpleMomentum,
    StrategyContext,
)
from quant_terminal.core.exceptions import DomainError, FutureDataError
from quant_terminal.core.identifiers import InstrumentId, OrderId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.data.storage import DatasetStore
from quant_terminal.execution.models import ExecutionConfig, PerUnitCommission
from quant_terminal.orders.models import OrderIntent, Side, TradeType

SOURCE = Path(__file__).parents[2] / "examples" / "data" / "synthetic_xnys_2026.csv"
INSTRUMENT = InstrumentMeta(
    InstrumentId("ETF-1"),
    "TEST",
    "USD",
    "etf",
    "XNYS",
    "America/New_York",
    Decimal("0.01"),
    Decimal("1"),
)
START = datetime(2026, 11, 25, 14, 30, tzinfo=UTC)
END = datetime(2026, 11, 28, 0, 0, tzinfo=UTC)


def fixture_engine(tmp_path: Path, strategy, *, max_notional: str = "1000000"):
    store = DatasetStore(tmp_path)
    snapshot = store.import_csv(
        SOURCE,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    config = BacktestConfig(
        initial_cash=Decimal("10000"),
        currency="USD",
        max_order_notional=Decimal(max_notional),
    )
    execution = ExecutionConfig(commission=PerUnitCommission(Decimal("0.1")))
    return BacktestEngine(store, (snapshot,), strategy, config, execution), snapshot


def write_rows(path: Path, instrument_id: str, rows: list[dict[str, str]]) -> None:
    import csv

    fields = (
        "instrument_id",
        "frequency",
        "start",
        "end",
        "available_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "source",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({**row, "instrument_id": instrument_id} for row in rows)


def second_instrument() -> InstrumentMeta:
    return InstrumentMeta(
        InstrumentId("ETF-2"),
        "TEST2",
        "USD",
        "etf",
        "XNYS",
        "America/New_York",
        Decimal("0.01"),
        Decimal("1"),
    )


def test_buy_and_hold_matches_hand_calculated_orders_costs_and_nav(tmp_path: Path) -> None:
    engine, snapshot = fixture_engine(tmp_path, BuyAndHold(INSTRUMENT.instrument_id, Decimal("10")))
    result = engine.run(start=START, end=END, benchmark=INSTRUMENT.instrument_id)
    assert snapshot.row_count == 2
    assert len(result.nav) == 3
    assert [point.nav for point in result.nav] == [
        Decimal("10000"),
        Decimal("10000"),
        Decimal("10001.5"),
    ]
    assert result.nav[0].period_return is None
    assert result.nav[1].period_return == 0
    assert result.nav[2].period_return == Decimal("0.00015")
    assert result.nav[2].cash == Decimal("8996.5")
    assert result.nav[2].positions[0].quantity == Decimal("10")
    assert result.nav[2].positions[0].mark_price == Decimal("100.50")
    assert result.nav[2].reconciliation_error == 0
    assert len(result.orders) == 1
    assert result.orders[0].fills[0].timestamp == datetime(2026, 11, 27, 14, 30, tzinfo=UTC)
    assert result.orders[0].fills[0].observed_at == datetime(2026, 11, 27, 18, 1, tzinfo=UTC)
    assert result.commission_cost == Decimal("1.0")
    assert result.slippage_cost == 0
    assert result.trades[0].quantity == Decimal("10")
    assert len(result.benchmark) == 2
    assert result.benchmark[-1].price == Decimal("100.50")
    assert result.benchmark[-1].price_return_only is True
    assert result.cash[-1][1] == Decimal("8996.5")
    assert result.positions[-1][1][0].quantity == Decimal("10")
    assert result.returns[-1][1] == Decimal("0.00015")
    assert result.exposures[-1][2] == Decimal("1005.00")
    assert dict(result.metadata)["cost_assumption"] == "configured_costs"
    assert dict(result.metadata)["allow_zero_costs"] == "False"


def test_pretrade_risk_rejection_is_recorded_and_has_no_fill(tmp_path: Path) -> None:
    engine, _ = fixture_engine(
        tmp_path,
        BuyAndHold(INSTRUMENT.instrument_id, Decimal("10")),
        max_notional="500",
    )
    first_availability = datetime(2026, 11, 25, 21, 1, tzinfo=UTC)
    result = engine.run(start=START, end=first_availability + timedelta(seconds=1))
    assert result.orders[0].status.value == "rejected"
    assert result.orders[0].reason == "order notional exceeds configured limit"
    assert result.orders[0].accepted_at is None
    assert result.orders[0].fills == ()
    assert result.nav[-1].nav == Decimal("10000")


def test_strategy_future_history_request_is_rejected(tmp_path: Path) -> None:
    class FutureProbe:
        name = "future_probe"

        def on_bar(self, context: StrategyContext, bar):
            context.history(
                bar.instrument_id,
                10,
                end=context.current_time + timedelta(seconds=1),
            )
            return ()

    engine, _ = fixture_engine(tmp_path, FutureProbe())
    with pytest.raises(FutureDataError, match="extends beyond simulation"):
        engine.run(start=START, end=END)


def test_no_trade_period_preserves_cash_and_returns_zero(tmp_path: Path) -> None:
    class NoTrade:
        name = "no_trade"

        def on_bar(self, context: StrategyContext, bar):
            return ()

    engine, _ = fixture_engine(tmp_path, NoTrade())
    result = engine.run(start=START, end=END)
    assert not result.orders
    assert not result.fills
    assert [point.nav for point in result.nav] == [Decimal("10000")] * 3
    assert result.nav[1].period_return == 0
    assert result.nav[2].period_return == 0
    assert result.total_cost == 0


def test_explicit_zero_cost_assumption_is_required_and_recorded(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path)
    snapshot = store.import_csv(
        SOURCE,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    config = BacktestConfig(Decimal("10000"), "USD", allow_zero_costs=True)
    result = BacktestEngine(
        store,
        (snapshot,),
        BuyAndHold(INSTRUMENT.instrument_id, Decimal("1")),
        config,
    ).run(start=START, end=END)
    assert dict(result.metadata)["cost_assumption"] == "ZERO_COST_ASSUMPTION"
    assert dict(result.metadata)["allow_zero_costs"] == "True"
    assert result.total_cost == 0


def test_unlabeled_zero_cost_default_is_rejected(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path)
    snapshot = store.import_csv(
        SOURCE,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    with pytest.raises(DomainError, match="allow_zero_costs"):
        BacktestEngine(
            store,
            (snapshot,),
            BuyAndHold(INSTRUMENT.instrument_id, Decimal("1")),
            BacktestConfig(Decimal("10000"), "USD"),
        )


def test_custom_zero_cost_models_must_declare_assumption(tmp_path: Path) -> None:
    class FreeCommission:
        is_zero_cost = True

        def incremental_fee(self, cumulative_quantity, fill_quantity, already_charged):
            return Decimal(0)

    class NoSlippage:
        is_zero_cost = True

        def amount(self, reference, participation):
            return Decimal(0)

    store = DatasetStore(tmp_path)
    snapshot = store.import_csv(
        SOURCE,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    execution = ExecutionConfig(commission=FreeCommission(), slippage=NoSlippage())
    with pytest.raises(DomainError, match="allow_zero_costs"):
        BacktestEngine(
            store,
            (snapshot,),
            BuyAndHold(INSTRUMENT.instrument_id, Decimal("1")),
            BacktestConfig(Decimal("10000"), "USD"),
            execution,
        )


def test_unaffordable_order_is_rejected_before_acceptance(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path)
    snapshot = store.import_csv(
        SOURCE,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    config = BacktestConfig(Decimal("50"), "USD")
    execution = ExecutionConfig(commission=PerUnitCommission(Decimal("0.1")))
    engine = BacktestEngine(
        store,
        (snapshot,),
        BuyAndHold(INSTRUMENT.instrument_id, Decimal("1")),
        config,
        execution,
    )
    result = engine.run(start=START, end=END)
    assert result.orders
    assert result.orders[0].status.value == "rejected"
    assert "cash" in (result.orders[0].reason or "")
    assert result.nav[-1].nav == Decimal("50")


def test_market_order_fill_gap_is_checked_at_actual_fill_price(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "store")
    rows = [
        {
            "frequency": "1d",
            "start": "2026-11-25T14:30:00+00:00",
            "end": "2026-11-25T21:00:00+00:00",
            "available_at": "2026-11-25T21:01:00+00:00",
            "open": "100.00",
            "high": "100.00",
            "low": "100.00",
            "close": "100.00",
            "volume": "1000",
            "source": "synthetic-fixture",
        },
        {
            "frequency": "1d",
            "start": "2026-11-27T14:30:00+00:00",
            "end": "2026-11-27T18:00:00+00:00",
            "available_at": "2026-11-27T18:01:00+00:00",
            "open": "200.00",
            "high": "200.00",
            "low": "200.00",
            "close": "200.00",
            "volume": "500",
            "source": "synthetic-fixture",
        },
    ]
    csv_path = tmp_path / "gap.csv"
    write_rows(csv_path, INSTRUMENT.instrument_id.value, rows)
    snapshot = store.import_csv(
        csv_path,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )

    class CappedMarketOne:
        name = "capped_market_one"

        def on_bar(self, context: StrategyContext, bar):
            if context.has_active_order(bar.instrument_id):
                return ()
            return (
                OrderIntent(
                    OrderId("limit-one-" + context.current_time.strftime("%Y%m%dT%H%M%S%f")),
                    INSTRUMENT,
                    Side.BUY,
                    TradeType.MARKET,
                    Decimal("1"),
                    context.current_time,
                    max_execution_price=Decimal("150.00"),
                ),
            )

    config = BacktestConfig(Decimal("260"), "USD", max_order_notional=Decimal("150"))
    execution = ExecutionConfig(commission=PerUnitCommission(Decimal("0.1")))
    result = BacktestEngine(store, (snapshot,), CappedMarketOne(), config, execution).run(
        start=START, end=END
    )
    assert not result.fills
    assert result.orders[0].status.value == "rejected"
    assert result.orders[0].fills == ()
    assert result.nav[-1].cash == Decimal("260")


def test_max_order_notional_uses_market_buy_price_cap_at_admission(tmp_path: Path) -> None:
    engine, _ = fixture_engine(
        tmp_path,
        BuyAndHold(INSTRUMENT.instrument_id, Decimal("10")),
        max_notional="1200",
    )

    class CappedBuy:
        name = "capped_buy"

        def on_bar(self, context: StrategyContext, bar):
            return (
                OrderIntent(
                    OrderId("capped-notional-" + context.current_time.strftime("%Y%m%dT%H%M%S")),
                    INSTRUMENT,
                    Side.BUY,
                    TradeType.MARKET,
                    Decimal("10"),
                    context.current_time,
                    max_execution_price=Decimal("150.00"),
                ),
            )

    engine.strategy = CappedBuy()
    result = engine.run(start=START, end=END)
    assert result.orders[0].status.value == "rejected"
    assert result.orders[0].reason == "order notional exceeds configured limit"


def test_expired_buy_releases_its_cash_reservation(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path)
    snapshot = store.import_csv(
        SOURCE,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )

    class ReplaceExpired:
        name = "replace_expired"

        def on_bar(self, context: StrategyContext, bar):
            expiry = datetime(2026, 11, 26, tzinfo=UTC)
            return (
                OrderIntent(
                    OrderId("replacement-" + context.current_time.strftime("%Y%m%dT%H%M%S")),
                    INSTRUMENT,
                    Side.BUY,
                    TradeType.LIMIT,
                    Decimal("1"),
                    context.current_time,
                    limit_price=Decimal("100.00"),
                    expires_at=expiry if context.current_time < expiry else None,
                ),
            )

    execution = ExecutionConfig(commission=PerUnitCommission(Decimal("0.1")))
    result = BacktestEngine(
        store,
        (snapshot,),
        ReplaceExpired(),
        BacktestConfig(Decimal("150"), "USD"),
        execution,
    ).run(start=START, end=END)
    assert len(result.orders) == 2
    assert result.orders[0].status.value == "expired"
    assert result.orders[1].status.value == "accepted"


def test_gapped_stop_cannot_spend_another_buy_order_reservation(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "store")
    rows = [
        {
            "frequency": "1d",
            "start": "2026-11-25T14:30:00+00:00",
            "end": "2026-11-25T21:00:00+00:00",
            "available_at": "2026-11-25T21:01:00+00:00",
            "open": "100.00",
            "high": "100.00",
            "low": "100.00",
            "close": "100.00",
            "volume": "1000",
            "source": "synthetic-fixture",
        },
        {
            "frequency": "1d",
            "start": "2026-11-27T14:30:00+00:00",
            "end": "2026-11-27T18:00:00+00:00",
            "available_at": "2026-11-27T18:01:00+00:00",
            "open": "200.00",
            "high": "200.00",
            "low": "200.00",
            "close": "200.00",
            "volume": "500",
            "source": "synthetic-fixture",
        },
        {
            "frequency": "1d",
            "start": "2026-11-30T14:30:00+00:00",
            "end": "2026-11-30T21:00:00+00:00",
            "available_at": "2026-11-30T21:01:00+00:00",
            "open": "100.00",
            "high": "101.00",
            "low": "99.00",
            "close": "100.00",
            "volume": "1000",
            "source": "synthetic-fixture",
        },
    ]
    csv_path = tmp_path / "stop-reserve.csv"
    write_rows(csv_path, INSTRUMENT.instrument_id.value, rows)
    snapshot = store.import_csv(
        csv_path,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )

    class StopAndLimit:
        name = "stop_and_limit"

        def on_bar(self, context: StrategyContext, bar):
            if context.current_time != datetime(2026, 11, 25, 21, 1, tzinfo=UTC):
                return ()
            return (
                OrderIntent(
                    OrderId("gapped-stop"),
                    INSTRUMENT,
                    Side.BUY,
                    TradeType.STOP,
                    Decimal("1"),
                    context.current_time,
                    stop_price=Decimal("110.00"),
                    max_execution_price=Decimal("150.00"),
                ),
                OrderIntent(
                    OrderId("reserved-limit"),
                    INSTRUMENT,
                    Side.BUY,
                    TradeType.LIMIT,
                    Decimal("1"),
                    context.current_time,
                    limit_price=Decimal("100.00"),
                ),
            )

    execution = ExecutionConfig(commission=PerUnitCommission(Decimal("0.1")))
    result = BacktestEngine(
        store,
        (snapshot,),
        StopAndLimit(),
        BacktestConfig(Decimal("260"), "USD"),
        execution,
    ).run(start=START, end=datetime(2026, 12, 1, tzinfo=UTC))
    assert [order.status.value for order in result.orders] == ["rejected", "filled"]
    assert [fill.price for fill in result.fills] == [Decimal("100.00")]
    assert result.nav[-1].cash == Decimal("159.9")


def test_same_bar_gapped_buys_share_the_remaining_cash(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "store")
    rows = [
        {
            "frequency": "1d",
            "start": "2026-11-25T14:30:00+00:00",
            "end": "2026-11-25T21:00:00+00:00",
            "available_at": "2026-11-25T21:01:00+00:00",
            "open": "100.00",
            "high": "100.00",
            "low": "100.00",
            "close": "100.00",
            "volume": "1000",
            "source": "synthetic-fixture",
        },
        {
            "frequency": "1d",
            "start": "2026-11-27T14:30:00+00:00",
            "end": "2026-11-27T18:00:00+00:00",
            "available_at": "2026-11-27T18:01:00+00:00",
            "open": "200.00",
            "high": "200.00",
            "low": "200.00",
            "close": "200.00",
            "volume": "500",
            "source": "synthetic-fixture",
        },
    ]
    csv_path = tmp_path / "same-bar-gap.csv"
    write_rows(csv_path, INSTRUMENT.instrument_id.value, rows)
    snapshot = store.import_csv(
        csv_path,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )

    class TwoMarketBuys:
        name = "two_market_buys"

        def on_bar(self, context: StrategyContext, bar):
            if context.current_time != datetime(2026, 11, 25, 21, 1, tzinfo=UTC):
                return ()
            return tuple(
                OrderIntent(
                    OrderId(f"samebar-{index}"),
                    INSTRUMENT,
                    Side.BUY,
                    TradeType.MARKET,
                    Decimal("1"),
                    context.current_time,
                    max_execution_price=Decimal("200.00"),
                )
                for index in range(2)
            )

    config = BacktestConfig(Decimal("400.3"), "USD")
    execution = ExecutionConfig(commission=PerUnitCommission(Decimal("0.1")))
    result = BacktestEngine(store, (snapshot,), TwoMarketBuys(), config, execution).run(
        start=START, end=END
    )
    assert len(result.fills) == 2
    assert not any(order.status.value == "rejected" for order in result.orders)
    assert result.nav[-1].cash == Decimal("0.1")
    assert result.nav[-1].cash >= 0


def test_duplicate_strategy_order_ids_abort_run(tmp_path: Path) -> None:
    class DuplicateOrder:
        name = "duplicate_order"

        def on_bar(self, context: StrategyContext, bar):
            return (
                OrderIntent(
                    OrderId("same-id"),
                    INSTRUMENT,
                    Side.BUY,
                    TradeType.LIMIT,
                    Decimal("1"),
                    context.current_time,
                    limit_price=Decimal("100.00"),
                ),
            )

    engine, _ = fixture_engine(tmp_path, DuplicateOrder())
    with pytest.raises(DomainError, match="reused an order_id"):
        engine.run(start=START, end=END)


def test_two_instruments_at_same_availability_share_cash_reservations(tmp_path: Path) -> None:
    store = DatasetStore(tmp_path / "store")
    other = second_instrument()
    snapshot_a = store.import_csv(
        SOURCE,
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    csv_b = tmp_path / "other.csv"
    rows = [
        {
            "frequency": "1d",
            "start": "2026-11-25T14:30:00+00:00",
            "end": "2026-11-25T21:00:00+00:00",
            "available_at": "2026-11-25T21:01:00+00:00",
            "open": "80.00",
            "high": "80.00",
            "low": "80.00",
            "close": "80.00",
            "volume": "1000",
            "source": "synthetic-fixture",
        }
    ]
    write_rows(csv_b, other.instrument_id.value, rows)
    snapshot_b = store.import_csv(
        csv_b,
        instrument=other,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )

    class BuyBoth:
        name = "buy_both"

        def on_bar(self, context: StrategyContext, bar):
            if context.current_time != datetime(2026, 11, 25, 21, 1, tzinfo=UTC):
                return ()
            return (
                OrderIntent(
                    OrderId(f"buy-{bar.instrument_id.value}"),
                    context.instrument(bar.instrument_id),
                    Side.BUY,
                    TradeType.LIMIT,
                    Decimal("1"),
                    context.current_time,
                    limit_price=bar.close,
                ),
            )

    config = BacktestConfig(Decimal("120"), "USD")
    execution = ExecutionConfig(commission=PerUnitCommission(Decimal("0.1")))
    result = BacktestEngine(store, (snapshot_a, snapshot_b), BuyBoth(), config, execution).run(
        start=START, end=END
    )
    assert len(result.orders) == 2
    assert sum(order.status.value == "rejected" for order in result.orders) == 1
    assert len(result.fills) <= 1
    assert result.nav[-1].cash >= 0


@pytest.mark.parametrize(
    "strategy_factory",
    [
        lambda: MovingAverageCross(INSTRUMENT, Decimal("1"), fast_period=1, slow_period=2),
        lambda: SimpleMomentum(INSTRUMENT, Decimal("1"), lookback=1),
    ],
)
def test_example_signal_strategies_use_only_observed_history(tmp_path: Path, strategy_factory):
    engine, _ = fixture_engine(tmp_path, strategy_factory())
    result = engine.run(start=START, end=END)
    assert len(result.orders) == 1
    assert result.orders[0].status.value == "accepted"
    assert result.orders[0].fills == ()
    assert result.nav[-1].nav == Decimal("10000")
