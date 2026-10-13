"""Deterministic golden fixtures for order lifecycle and bar execution."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

from quant_terminal.accounting.ledger import PortfolioLedger
from quant_terminal.core.enums import OrderStatus
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId, OrderId
from quant_terminal.data.model import Bar, InstrumentMeta
from quant_terminal.execution.fills import FillDecision
from quant_terminal.execution.models import (
    ConfiguredSlippage,
    ExecutionConfig,
    PerUnitCommission,
    SlippageKind,
)
from quant_terminal.execution.simulator import HistoricalBroker
from quant_terminal.orders.models import OrderIntent, Side, TradeType, to_ledger_fill

START = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)


def instrument() -> InstrumentMeta:
    return InstrumentMeta(
        InstrumentId("ETF-1"),
        "TEST",
        "USD",
        "etf",
        "XNYS",
        "America/New_York",
        Decimal("0.01"),
        Decimal("1"),
    )


def bar(
    number: int,
    *,
    open_price: str = "10",
    high: str = "10.5",
    low: str = "9.5",
    close: str | None = None,
    volume: str = "100",
    frequency: str = "1m",
) -> Bar:
    start = START + timedelta(minutes=number)
    end = start + timedelta(minutes=1)
    return Bar(
        InstrumentId("ETF-1"),
        frequency,
        start,
        end,
        end,
        Decimal(open_price),
        Decimal(high),
        Decimal(low),
        Decimal(close if close is not None else open_price),
        Decimal(volume),
        "golden",
    )


def intent(
    number: int,
    side: Side = Side.BUY,
    quantity: str = "5",
    order_type: TradeType = TradeType.MARKET,
    *,
    created_at: datetime | None = None,
    limit: str | None = None,
    stop: str | None = None,
    expires_at: datetime | None = None,
) -> OrderIntent:
    return OrderIntent(
        OrderId(f"order-{number}"),
        instrument(),
        side,
        order_type,
        Decimal(quantity),
        created_at or START - timedelta(minutes=1),
        Decimal(limit) if limit is not None else None,
        Decimal(stop) if stop is not None else None,
        expires_at,
    )


def test_market_fill_lifecycle_commission_trade_and_ledger_handoff() -> None:
    broker = HistoricalBroker(
        ExecutionConfig(commission=PerUnitCommission(Decimal("0.1"), Decimal("0.5")))
    )
    submitted = broker.submit(intent(1))
    assert [event.status for event in submitted.events] == [
        OrderStatus.CREATED,
        OrderStatus.SUBMITTED,
        OrderStatus.ACCEPTED,
    ]
    fills = broker.process_bar(bar(0, open_price="10", volume="20"), bar(0).end)
    assert len(fills) == 1
    assert fills[0].quantity == Decimal("5")
    assert fills[0].price == Decimal("10")
    assert fills[0].commission == Decimal("0.5")
    complete = broker.order(OrderId("order-1"))
    assert complete.status is OrderStatus.FILLED
    assert complete.remaining_quantity == 0
    assert complete.trade is not None
    assert complete.trade.average_price == Decimal("10")
    assert complete.trade.commission == Decimal("0.5")

    ledger = PortfolioLedger(initial_cash=Decimal("1000"), currency="USD")
    portfolio = ledger.apply_fill(to_ledger_fill(fills[0]))
    assert portfolio.cash == Decimal("949.5")
    assert portfolio.positions[0].quantity == Decimal("5")
    assert portfolio.nav == Decimal("999.5")
    assert portfolio.reconciliation_error == 0


def test_partial_fill_capacity_is_shared_and_order_priority_is_deterministic() -> None:
    broker = HistoricalBroker(ExecutionConfig(maximum_participation=Decimal("0.5")))
    broker.submit(intent(1, quantity="4"))
    broker.submit(intent(2, quantity="4"))
    first_bar_fills = broker.process_bar(bar(0, volume="10"), bar(0).end)
    assert [(fill.order_id.value, fill.quantity) for fill in first_bar_fills] == [
        ("order-1", Decimal("4")),
        ("order-2", Decimal("1")),
    ]
    assert broker.order(OrderId("order-2")).status is OrderStatus.PARTIALLY_FILLED
    second = broker.process_bar(bar(1, volume="10"), bar(1).end)
    assert [(fill.order_id.value, fill.quantity) for fill in second] == [("order-2", Decimal("3"))]
    assert broker.order(OrderId("order-2")).status is OrderStatus.FILLED


@pytest.mark.parametrize(
    ("side", "open_price", "high", "low", "limit", "expected"),
    [
        (Side.BUY, "99", "101", "98", "100", "99"),
        (Side.BUY, "101", "102", "100", "100", "100"),
        (Side.SELL, "101", "102", "99", "100", "101"),
        (Side.SELL, "99", "100", "98", "100", "100"),
    ],
)
def test_limit_touch_and_gap_fill_prices(
    side: Side, open_price: str, high: str, low: str, limit: str, expected: str
) -> None:
    broker = HistoricalBroker()
    broker.submit(intent(1, side, order_type=TradeType.LIMIT, limit=limit))
    fills = broker.process_bar(bar(0, open_price=open_price, high=high, low=low), bar(0).end)
    assert len(fills) == 1
    assert fills[0].price == Decimal(expected)
    if side is Side.BUY:
        assert fills[0].price <= Decimal(limit)
    else:
        assert fills[0].price >= Decimal(limit)


@pytest.mark.parametrize(
    ("side", "open_price", "high", "low", "stop", "expected"),
    [
        (Side.BUY, "105", "106", "104", "101", "105"),
        (Side.BUY, "100", "102", "99", "101", "101"),
        (Side.SELL, "95", "96", "94", "99", "95"),
        (Side.SELL, "100", "101", "98", "99", "99"),
    ],
)
def test_stop_trigger_and_gap_prices(
    side: Side, open_price: str, high: str, low: str, stop: str, expected: str
) -> None:
    broker = HistoricalBroker()
    broker.submit(intent(1, side, order_type=TradeType.STOP, stop=stop))
    fills = broker.process_bar(bar(0, open_price=open_price, high=high, low=low), bar(0).end)
    assert len(fills) == 1
    assert fills[0].price == Decimal(expected)


def test_stop_limit_triggers_only_for_the_next_bar() -> None:
    broker = HistoricalBroker()
    broker.submit(intent(1, order_type=TradeType.STOP_LIMIT, limit="101", stop="100"))
    trigger_bar = bar(0, open_price="99", high="102", low="98")
    assert broker.process_bar(trigger_bar, trigger_bar.end) == ()
    eligible_bar = bar(1, open_price="100.5", high="101", low="100")
    fills = broker.process_bar(eligible_bar, eligible_bar.end)
    assert len(fills) == 1
    assert fills[0].price == Decimal("100.5")


def test_stop_trigger_latches_on_zero_volume_bar() -> None:
    broker = HistoricalBroker()
    broker.submit(intent(1, order_type=TradeType.STOP, stop="11"))
    no_liquidity = bar(0, open_price="10", high="11", low="9", volume="0")
    assert broker.process_bar(no_liquidity, no_liquidity.end) == ()
    next_bar = bar(1, open_price="10", high="10.5", low="9.5", volume="100")
    fills = broker.process_bar(next_bar, next_bar.end)
    assert len(fills) == 1
    assert fills[0].price == Decimal("10")


@pytest.mark.parametrize(("side", "expected"), [(Side.BUY, "10.01"), (Side.SELL, "9.99")])
def test_fixed_slippage_is_adverse_and_rounded_to_tick(side: Side, expected: str) -> None:
    broker = HistoricalBroker(
        ExecutionConfig(slippage=ConfiguredSlippage(SlippageKind.FIXED, Decimal("0.003")))
    )
    broker.submit(intent(1, side))
    fills = broker.process_bar(bar(0, open_price="10", high="10.5", low="9.5"), bar(0).end)
    assert fills[0].price == Decimal(expected)


def test_basis_point_and_volume_share_slippage() -> None:
    bps = HistoricalBroker(
        ExecutionConfig(slippage=ConfiguredSlippage(SlippageKind.BASIS_POINTS, Decimal("100")))
    )
    bps.submit(intent(1))
    fill_bps = bps.process_bar(bar(0, open_price="10", volume="100"), bar(0).end)[0]
    assert fill_bps.price == Decimal("10.10")
    volume = HistoricalBroker(
        ExecutionConfig(slippage=ConfiguredSlippage(SlippageKind.VOLUME_SHARE, Decimal("0.1")))
    )
    volume.submit(intent(1))
    fill_volume = volume.process_bar(bar(0, open_price="10", volume="100"), bar(0).end)[0]
    assert fill_volume.price == Decimal("10.01")


def test_cumulative_minimum_commission_is_charged_incrementally() -> None:
    broker = HistoricalBroker(
        ExecutionConfig(
            maximum_participation=Decimal("0.5"),
            commission=PerUnitCommission(Decimal("0.1"), Decimal("1")),
        )
    )
    broker.submit(intent(1, quantity="8"))
    first = broker.process_bar(bar(0, volume="10"), bar(0).end)
    second = broker.process_bar(bar(1, volume="10"), bar(1).end)
    assert [fill.commission for fill in (*first, *second)] == [Decimal("1"), Decimal("0")]
    assert broker.order(OrderId("order-1")).trade is not None
    assert broker.order(OrderId("order-1")).trade.commission == Decimal("1")


def test_cancel_expiry_duplicate_bar_and_reject_lifecycle() -> None:
    broker = HistoricalBroker()
    cancelled = broker.submit(intent(1, quantity="5"))
    result = broker.cancel(cancelled.intent.order_id, START)
    assert result.status is OrderStatus.CANCELLED
    assert result.trade is None
    expires = START + timedelta(minutes=1)
    broker.submit(intent(2, expires_at=expires), accepted_at=START)
    expired_bar = bar(1)
    assert broker.process_bar(expired_bar, expired_bar.end) == ()
    assert broker.order(OrderId("order-2")).status is OrderStatus.EXPIRED
    with pytest.raises(DomainError, match="only be processed once"):
        broker.process_bar(expired_bar, expired_bar.end)

    broker.submit(intent(3), accepted_at=expired_bar.end)
    rejected = broker.reject(OrderId("order-3"), expired_bar.end, "risk gate")
    assert rejected.status is OrderStatus.REJECTED
    with pytest.raises(DomainError, match="Duplicate order_id"):
        broker.submit(intent(3))


def test_no_lookahead_and_order_validation() -> None:
    broker = HistoricalBroker()
    same_bar = intent(1, created_at=START)
    broker.submit(same_bar, accepted_at=START + timedelta(seconds=1))
    assert broker.process_bar(bar(0), bar(0).end) == ()
    early_observation = HistoricalBroker()
    early_observation.submit(intent(2))
    with pytest.raises(DomainError, match="before it is observable"):
        early_observation.process_bar(bar(0), bar(0).end - timedelta(seconds=1))
    with pytest.raises(DomainError, match="quantity step"):
        intent(3, quantity="0.5")
    with pytest.raises(DomainError, match="limit_price"):
        intent(4, order_type=TradeType.LIMIT)


def test_backdated_order_actions_are_rejected_after_simulation_advances() -> None:
    broker = HistoricalBroker()
    first = bar(0)
    broker.process_bar(first, first.end)
    with pytest.raises(DomainError, match="backdated"):
        broker.submit(intent(1), accepted_at=first.end - timedelta(seconds=1))
    broker.submit(intent(2), accepted_at=first.end)
    with pytest.raises(DomainError, match="backdated"):
        broker.cancel(OrderId("order-2"), first.end - timedelta(seconds=1))


def test_execution_price_arithmetic_ignores_ambient_decimal_context() -> None:
    inst = InstrumentMeta(
        InstrumentId("ETF-1"),
        "TEST",
        "USD",
        "etf",
        "XNYS",
        "America/New_York",
        Decimal("0.0001"),
        Decimal("1"),
    )
    order = OrderIntent(
        OrderId("order-1"),
        inst,
        Side.BUY,
        TradeType.MARKET,
        Decimal("1"),
        START - timedelta(minutes=1),
    )
    current = bar(
        0,
        open_price="1.2345",
        high="1.3000",
        low="1.2000",
        volume="10",
    )
    broker = HistoricalBroker(
        ExecutionConfig(slippage=ConfiguredSlippage(SlippageKind.FIXED, Decimal("0.0001")))
    )
    broker.submit(order)
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        fills = broker.process_bar(current, current.end)
    assert fills[0].price == Decimal("1.2346")


def test_fill_model_is_replaceable() -> None:
    class NeverFill:
        def match(
            self,
            order: OrderIntent,
            market_bar: Bar,
            *,
            triggered: bool,
            trigger_ready_at: datetime | None,
        ) -> FillDecision:
            return FillDecision(triggered, trigger_ready_at, None, order.limit_price)

    broker = HistoricalBroker(fill_model=NeverFill())
    broker.submit(intent(1))
    assert broker.process_bar(bar(0), bar(0).end) == ()
    assert broker.order(OrderId("order-1")).status is OrderStatus.ACCEPTED


def test_commission_and_slippage_models_are_replaceable() -> None:
    class FreeCommission:
        def incremental_fee(
            self,
            cumulative_quantity: Decimal,
            fill_quantity: Decimal,
            already_charged: Decimal,
        ) -> Decimal:
            return Decimal(0)

    class ConstantSlippage:
        def amount(self, reference: Decimal, participation: Decimal) -> Decimal:
            return Decimal("0.01")

    broker = HistoricalBroker(
        ExecutionConfig(commission=FreeCommission(), slippage=ConstantSlippage())
    )
    broker.submit(intent(1))
    fills = broker.process_bar(bar(0, open_price="10"), bar(0).end)
    assert fills[0].price == Decimal("10.01")
    assert fills[0].commission == 0


def test_limit_price_protection_survives_adverse_slippage_and_invalid_sell_rejects() -> None:
    broker = HistoricalBroker(
        ExecutionConfig(slippage=ConfiguredSlippage(SlippageKind.BASIS_POINTS, Decimal("1000")))
    )
    broker.submit(intent(1, order_type=TradeType.LIMIT, limit="10"))
    bar0 = bar(0, open_price="10", high="10.5", low="9.5")
    buy_fill = broker.process_bar(bar0, bar0.end)
    assert buy_fill[0].price == Decimal("10")

    invalid = HistoricalBroker(
        ExecutionConfig(slippage=ConfiguredSlippage(SlippageKind.FIXED, Decimal("10")))
    )
    invalid.submit(intent(2, side=Side.SELL))
    bad_bar = bar(0, open_price="10", high="10.5", low="9.5")
    assert invalid.process_bar(bad_bar, bad_bar.end) == ()
    assert invalid.order(OrderId("order-2")).status is OrderStatus.REJECTED


def test_out_of_order_bar_is_rejected() -> None:
    broker = HistoricalBroker()
    broker.process_bar(bar(1), bar(1).end)
    with pytest.raises(DomainError, match="in increasing time order"):
        broker.process_bar(bar(0), bar(1).end)


def test_mixed_execution_frequencies_for_one_instrument_are_rejected() -> None:
    broker = HistoricalBroker()
    minute_bar = bar(0)
    broker.process_bar(minute_bar, minute_bar.end)
    daily_bar = bar(1, frequency="1d")
    with pytest.raises(DomainError, match="one execution frequency"):
        broker.process_bar(daily_bar, daily_bar.end)


def test_execution_assumption_bounds_are_validated() -> None:
    with pytest.raises(DomainError, match="nonnegative"):
        PerUnitCommission(Decimal("-0.1"))
    with pytest.raises(DomainError, match="at most 1"):
        ConfiguredSlippage(SlippageKind.VOLUME_SHARE, Decimal("1.1"))
    with pytest.raises(DomainError, match="maximum_participation"):
        ExecutionConfig(maximum_participation=Decimal("1.1"))
