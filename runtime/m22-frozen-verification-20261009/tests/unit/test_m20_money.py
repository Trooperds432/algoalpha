"""Independent native FX, FIFO, financing and borrow accounting fixtures."""

from decimal import ROUND_UP, Decimal, Inexact, localcontext

import pytest

from quant_terminal.advanced_assets.money import (
    borrow_availability,
    borrow_fee,
    financing,
    fx_conversion,
    multi_currency_account,
)
from quant_terminal.core.exceptions import DomainError

D = Decimal


def trade(
    identity: str,
    quantity: str,
    price: str,
    *,
    fee: str = "0",
    borrow: str = "0",
    currency: str = "EUR",
) -> dict[str, object]:
    return {
        "event_id": identity,
        "kind": "trade",
        "asset_id": "ETF",
        "currency": currency,
        "asset_class": "etf",
        "quantity": D(quantity),
        "price": D(price),
        "fee": D(fee),
        "borrow_available": D(borrow),
    }


def test_fx_direction_fee_and_native_balances() -> None:
    result = fx_conversion("USD", "EUR", D(100), D(".9"), D(1))
    assert result["source_debit"] == D(101)
    assert result["target_credit"] == D(90)
    event = {
        "event_id": "fx-1",
        "kind": "fx",
        "source_currency": "USD",
        "target_currency": "EUR",
        "quantity": D(100),
        "rate": D(".9"),
        "fee": D(1),
    }
    result = multi_currency_account(
        "USD", {"USD": D(1000)}, {"USD": D(1), "EUR": D("1.1")}, (event,)
    )
    assert result["native_cash"] == {"USD": D(899), "EUR": D(90)}
    assert result["base_nav"] == D(998)
    assert result["total_base_pnl"] == D(-2)  # $1 fee, $1 quote/valuation difference


def test_multicurrency_fifo_and_mark_independent_nav() -> None:
    events = (trade("buy", "10", "5", fee="1"), trade("sell", "-4", "7", fee="1"))
    result = multi_currency_account(
        "USD", {"USD": D(1000), "EUR": D(100)}, {"USD": D(1), "EUR": D("1.2")}, events
    )
    assert result["native_cash"] == {"USD": D(1000), "EUR": D(76)}
    assert result["native_realised_pnl"] == {"EUR": D(6)}
    assert result["positions"][0]["native_unrealised_pnl"] == D(12)
    assert result["base_nav"] == D("1141.6")
    assert result["total_base_pnl"] == D("21.6")


def test_fx_revaluation_and_event_time_external_flows() -> None:
    events = (
        {"event_id": "deposit", "kind": "cash", "currency": "EUR", "quantity": D(10)},
        {"event_id": "rate", "kind": "fx_rate", "currency": "EUR", "rate": D("1.3")},
    )
    result = multi_currency_account("USD", {"EUR": D(100)}, {"USD": D(1), "EUR": D("1.2")}, events)
    assert result["external_flows_base"] == D(12)
    assert result["base_nav"] == D(143)
    assert result["total_base_pnl"] == D(11)


def test_fifo_lots_and_crossing_from_long_to_short() -> None:
    events = (
        trade("buy-1", "3", "5", currency="USD"),
        trade("buy-2", "2", "8", currency="USD"),
        trade("sell", "-4", "10", currency="USD"),
    )
    result = multi_currency_account("USD", {"USD": D(100)}, {"USD": D(1)}, events)
    assert result["native_realised_pnl"] == {"USD": D(17)}
    assert result["positions"][0]["native_unrealised_pnl"] == D(2)
    assert result["base_nav"] == D(119)
    result = multi_currency_account(
        "USD",
        {"USD": D(100)},
        {"USD": D(1)},
        (
            trade("buy", "3", "10", currency="USD"),
            trade("cross", "-5", "12", borrow="2", currency="USD"),
        ),
    )
    assert result["positions"][0]["quantity"] == D(-2)
    assert result["native_realised_pnl"] == {"USD": D(6)}
    assert result["base_nav"] == D(106)


def test_short_borrow_cover_and_crossing_lots() -> None:
    events = (
        trade("short", "-5", "10", borrow="5", currency="USD"),
        trade("cover", "2", "8", currency="USD"),
    )
    result = multi_currency_account("USD", {"USD": D(100)}, {"USD": D(1)}, events)
    assert result["native_realised_pnl"] == {"USD": D(4)}
    assert result["positions"][0]["native_unrealised_pnl"] == D(6)
    assert result["base_nav"] == D(110)
    with pytest.raises(DomainError, match="borrow"):
        multi_currency_account(
            "USD",
            {"USD": D(100)},
            {"USD": D(1)},
            (trade("short", "-5", "10", borrow="4", currency="USD"),),
        )


def test_carry_costs_golden_and_application() -> None:
    assert borrow_availability(D(5), D(4))["shortfall"] == D(1)
    assert borrow_availability(D(4), D(4))["approved"] is True
    assert borrow_fee(D(100), D(10), D(".365"), D(10))["borrow_fee"] == D(10)
    result = financing(D(-1000), D(".365"), D(".1"), D(10))
    assert result["debit_interest"] == D(10)
    assert result["cash_change"] == D(-10)
    assert financing(D(1000), D(".3"), D(".365"), D(10))["credit_interest"] == D(10)
    account = multi_currency_account(
        "USD",
        {"USD": D(100)},
        {"USD": D(1)},
        ({"event_id": "borrow-cost", "kind": "expense", "currency": "USD", "quantity": D(10)},),
    )
    assert account["base_nav"] == D(90)
    assert account["external_flows_base"] == D(0)
    assert account["total_base_pnl"] == D(-10)


def test_context_independence_and_no_input_mutation() -> None:
    balances, rates = {"USD": D(100)}, {"USD": D(1)}
    baseline = multi_currency_account("USD", balances, rates, ())
    with localcontext() as ctx:
        ctx.prec = 4
        ctx.rounding = ROUND_UP
        ctx.traps[Inexact] = True
        assert multi_currency_account("USD", balances, rates, ()) == baseline
        assert financing(D(-1000), D(".365"), D(0), D(10))["debit_interest"] == D(10)
    assert balances == {"USD": D(100)}


def test_zero_terminal_equity_mark_is_valid() -> None:
    result = multi_currency_account(
        "USD",
        {"USD": D(100)},
        {"USD": D(1)},
        (
            trade("buy", "10", "10", currency="USD"),
            {"event_id": "bankruptcy", "kind": "mark", "asset_id": "ETF", "price": D(0)},
        ),
    )
    assert result["base_nav"] == D(0)
    assert result["total_base_pnl"] == D(-100)


@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D("1e1000"), 0.1, True])
def test_bad_financial_inputs_reject(value: object) -> None:
    with pytest.raises(DomainError):
        borrow_availability(value, D(1))  # type: ignore[arg-type]


def test_unknown_quotes_ids_modes_and_fee_reject() -> None:
    with pytest.raises(DomainError, match="quote"):
        multi_currency_account("USD", {"EUR": D(1)}, {"USD": D(1)}, ())
    e = {"event_id": "x", "kind": "cash", "currency": "USD", "quantity": D(1)}
    with pytest.raises(DomainError, match="Duplicate"):
        multi_currency_account("USD", {"USD": D(1)}, {"USD": D(1)}, (e, e))
    e = trade("derivative", "1", "10", currency="USD")
    e["asset_class"] = "future"
    with pytest.raises(DomainError, match="derivative"):
        multi_currency_account("USD", {"USD": D(100)}, {"USD": D(1)}, (e,))
    with pytest.raises(DomainError):
        fx_conversion("USD", "USD", D(1), D(1))
    with pytest.raises(DomainError):
        borrow_fee(D(1), D(1), D(".1"), D(1), D(252))
