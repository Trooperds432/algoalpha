"""Hand-calculated FIFO portfolio accounting fixtures."""

from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quant_terminal.accounting.ledger import FillSide, LedgerFill, PortfolioLedger
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import FillId, InstrumentId
from quant_terminal.data.model import InstrumentMeta


def instrument(currency: str = "USD", identifier: str = "ETF-1") -> InstrumentMeta:
    return InstrumentMeta(
        InstrumentId(identifier),
        "TEST",
        currency,
        "etf",
        "XNYS",
        "America/New_York",
        Decimal("0.01"),
        Decimal("1"),
    )


def fill(
    number: int,
    side: FillSide,
    quantity: str,
    price: str,
    fee: str = "0",
    inst: InstrumentMeta | None = None,
) -> LedgerFill:
    return LedgerFill(
        FillId(f"fill-{number}"),
        inst or instrument(),
        side,
        Decimal(quantity),
        Decimal(price),
        Decimal(fee),
        datetime(2026, 1, 5, 15, 0, tzinfo=UTC),
    )


def test_long_entry_mark_partial_exit_and_reconciliation() -> None:
    ledger = PortfolioLedger(initial_cash=Decimal("10000"), currency="USD")
    opened = ledger.apply_fill(fill(1, FillSide.BUY, "10", "100", "1"))
    assert opened.cash == Decimal("8999")
    assert opened.nav == Decimal("9999")
    assert opened.realized_pnl == Decimal("0")
    assert opened.unrealized_pnl == Decimal("-1")
    marked = ledger.mark(InstrumentId("ETF-1"), Decimal("110"))
    assert marked.nav == Decimal("10099")
    assert marked.unrealized_pnl == Decimal("99")

    closed = ledger.apply_fill(fill(2, FillSide.SELL, "4", "110", "0.4"))
    position = closed.positions[0]
    assert closed.cash == Decimal("9438.6")
    assert position.quantity == Decimal("6")
    assert position.market_value == Decimal("660")
    assert closed.nav == Decimal("10098.6")
    assert closed.realized_pnl == Decimal("39.2")
    assert closed.unrealized_pnl == Decimal("59.4")
    assert closed.reconciliation_error == 0


def test_multiple_fifo_lots_allocate_entry_and_exit_fees_exactly() -> None:
    ledger = PortfolioLedger(initial_cash=Decimal("10000"), currency="USD")
    ledger.apply_fill(fill(1, FillSide.BUY, "5", "100", "0.5"))
    ledger.apply_fill(fill(2, FillSide.BUY, "5", "102", "0.51"))
    result = ledger.apply_fill(fill(3, FillSide.SELL, "7", "105", "0.74"))
    assert result.cash == Decimal("9723.25")
    assert result.positions[0].quantity == Decimal("3")
    assert result.positions[0].average_entry_price == Decimal("102")
    assert result.realized_pnl == Decimal("29.556")
    assert result.unrealized_pnl == Decimal("8.694")
    assert result.nav == Decimal("10038.25")
    assert result.realized_pnl + result.unrealized_pnl == result.nav - Decimal("10000")
    assert result.reconciliation_error == 0


def test_short_entry_and_cover_require_explicit_research_assumption() -> None:
    with pytest.raises(DomainError):
        PortfolioLedger(initial_cash=Decimal("10000"), currency="USD", allow_short=True)
    ledger = PortfolioLedger(
        initial_cash=Decimal("10000"),
        currency="USD",
        allow_short=True,
        short_assumption="synthetic research short; no borrow fee modeled",
    )
    ledger.apply_fill(fill(1, FillSide.SELL, "10", "100", "1"))
    marked = ledger.mark(InstrumentId("ETF-1"), Decimal("90"))
    assert marked.positions[0].quantity == Decimal("-10")
    assert marked.positions[0].market_value == Decimal("-900")
    assert marked.nav == Decimal("10099")
    assert marked.unrealized_pnl == Decimal("99")
    covered = ledger.apply_fill(fill(2, FillSide.BUY, "4", "90", "0.4"))
    assert covered.positions[0].quantity == Decimal("-6")
    assert covered.realized_pnl == Decimal("39.2")
    assert covered.unrealized_pnl == Decimal("59.4")
    assert covered.reconciliation_error == 0


def test_reversal_closes_fifo_long_before_opening_short() -> None:
    ledger = PortfolioLedger(
        initial_cash=Decimal("10000"),
        currency="USD",
        allow_short=True,
        short_assumption="synthetic research short; no borrow fee modeled",
    )
    ledger.apply_fill(fill(1, FillSide.BUY, "4", "100", "0.4"))
    reversed_position = ledger.apply_fill(fill(2, FillSide.SELL, "6", "110", "0.66"))
    ledger.mark(InstrumentId("ETF-1"), Decimal("105"))
    result = ledger.snapshot()
    assert reversed_position.positions[0].quantity == Decimal("-2")
    assert result.cash == Decimal("10258.94")
    assert result.positions[0].average_entry_price == Decimal("110")
    assert result.realized_pnl == Decimal("39.16")
    assert result.unrealized_pnl == Decimal("9.78")
    assert result.nav == Decimal("10048.94")
    assert result.reconciliation_error == 0


def test_owner_brief_reversal_fixture_reconciles() -> None:
    ledger = PortfolioLedger(
        initial_cash=Decimal("10000"),
        currency="USD",
        allow_short=True,
        short_assumption="synthetic research short; no borrow fee modeled",
    )
    ledger.apply_fill(fill(1, FillSide.BUY, "10", "100", "1"))
    ledger.apply_fill(fill(2, FillSide.SELL, "15", "110", "1.5"))
    ledger.mark(InstrumentId("ETF-1"), Decimal("110"))
    result = ledger.snapshot()
    assert result.positions[0].quantity == Decimal("-5")
    assert result.cash == Decimal("10647.5")
    assert result.nav == Decimal("10097.5")
    assert result.realized_pnl == Decimal("98")
    assert result.unrealized_pnl == Decimal("-0.5")
    assert result.reconciliation_error == 0


def test_rejected_fills_are_atomic_and_duplicate_ids_do_not_reapply() -> None:
    ledger = PortfolioLedger(initial_cash=Decimal("1000"), currency="USD")
    original = fill(1, FillSide.BUY, "2", "100", "0.2")
    ledger.apply_fill(original)
    before = ledger.snapshot()
    with pytest.raises(DomainError, match="Duplicate"):
        ledger.apply_fill(original)
    with pytest.raises(DomainError, match="unauthorized short"):
        ledger.apply_fill(fill(2, FillSide.SELL, "3", "100"))
    with pytest.raises(DomainError, match="currency"):
        ledger.apply_fill(fill(3, FillSide.BUY, "1", "100", inst=instrument("GBP", "ETF-2")))
    with pytest.raises(DomainError, match="tick size"):
        ledger.apply_fill(fill(4, FillSide.BUY, "1", "100.005"))
    with pytest.raises(DomainError, match="quantity step"):
        ledger.apply_fill(fill(5, FillSide.BUY, "0.5", "100"))
    with pytest.raises(DomainError, match="without an open position"):
        ledger.mark(InstrumentId("UNKNOWN"), Decimal("100"))
    assert ledger.snapshot() == before
    assert len(ledger.fills) == 1


def test_zero_or_negative_initial_cash_and_invalid_short_settings_reject() -> None:
    with pytest.raises(DomainError):
        PortfolioLedger(initial_cash=Decimal("0"), currency="USD")
    with pytest.raises(DomainError):
        PortfolioLedger(initial_cash=Decimal("10"), currency="usd")
    with pytest.raises(DomainError):
        PortfolioLedger(initial_cash=Decimal("10"), currency="USD", short_assumption="unused")


@given(
    quantity=st.integers(min_value=1, max_value=100),
    entry_cents=st.integers(min_value=10000, max_value=50000),
    exit_cents=st.integers(min_value=10000, max_value=50000),
    exit_quantity=st.integers(min_value=1, max_value=100),
    entry_fee_cents=st.integers(min_value=0, max_value=500),
    exit_fee_cents=st.integers(min_value=0, max_value=500),
)
def test_long_ledger_reconciles_generated_partial_and_full_exits(
    quantity: int,
    entry_cents: int,
    exit_cents: int,
    exit_quantity: int,
    entry_fee_cents: int,
    exit_fee_cents: int,
) -> None:
    exit_quantity = min(exit_quantity, quantity)
    entry_price = Decimal(entry_cents) / Decimal(100)
    exit_price = Decimal(exit_cents) / Decimal(100)
    entry_fee = Decimal(entry_fee_cents) / Decimal(100)
    exit_fee = Decimal(exit_fee_cents) / Decimal(100)
    ledger = PortfolioLedger(initial_cash=Decimal("100000"), currency="USD")
    ledger.apply_fill(fill(1, FillSide.BUY, str(quantity), str(entry_price), str(entry_fee)))
    result = ledger.apply_fill(
        fill(2, FillSide.SELL, str(exit_quantity), str(exit_price), str(exit_fee))
    )
    remaining = quantity - exit_quantity
    expected_cash = (
        Decimal("100000")
        - Decimal(quantity) * entry_price
        - entry_fee
        + Decimal(exit_quantity) * exit_price
        - exit_fee
    )
    expected_nav = expected_cash + Decimal(remaining) * exit_price
    assert result.cash == expected_cash
    assert result.nav == expected_nav
    assert result.reconciliation_error == 0
    assert result.realized_pnl + result.unrealized_pnl == expected_nav - Decimal("100000")


def test_ledger_precision_is_independent_of_callers_decimal_context() -> None:
    ledger = PortfolioLedger(initial_cash=Decimal("1000"), currency="USD")
    fractional = InstrumentMeta(
        InstrumentId("ETF-F"),
        "TEST",
        "USD",
        "etf",
        "XNYS",
        "America/New_York",
        Decimal("0.01"),
        Decimal("0.01"),
    )
    with localcontext() as caller_context:
        caller_context.prec = 2
        caller_context.rounding = ROUND_DOWN
        opened = ledger.apply_fill(fill(1, FillSide.BUY, "1.23", "10", "0.07", fractional))
        assert opened.positions[0].average_entry_price == Decimal("10")
        result = ledger.apply_fill(fill(2, FillSide.SELL, "0.23", "11", "0.01", fractional))
        assert result.positions[0].quantity == Decimal("1.00")
        assert result.positions[0].average_entry_price == Decimal("10")
        assert result.cash == Decimal("990.15")
        assert result.nav == Decimal("1001.15")
        assert result.reconciliation_error == 0
        with localcontext() as expected_context:
            expected_context.prec = 34
            expected_entry_fee = Decimal("0.07") * Decimal("0.23") / Decimal("1.23")
            expected_realized = Decimal("0.23") - expected_entry_fee - Decimal("0.01")
        assert result.realized_pnl == expected_realized


def test_failed_mark_snapshot_rolls_back_mark() -> None:
    ledger = PortfolioLedger(initial_cash=Decimal("10"), currency="USD")
    ledger.apply_fill(fill(1, FillSide.BUY, "1", "1"))
    before = ledger.snapshot()
    with pytest.raises(ArithmeticError):
        ledger.mark(InstrumentId("ETF-1"), Decimal("1E+1000000"))
    assert ledger.snapshot() == before
