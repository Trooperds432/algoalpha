from decimal import ROUND_DOWN, Decimal, getcontext

import pytest

from quant_terminal.advanced_assets.futures import (
    continuous_contract,
    futures_margin,
    futures_pnl,
    futures_settlement,
)
from quant_terminal.core.exceptions import DomainError

D = Decimal


def test_pnl_signed_positions_multiplier_and_negative_prices() -> None:
    assert futures_pnl(3, D("50"), D("-2"), D("1"))["pnl"] == D("450")
    assert futures_pnl(-3, D("50"), D("-2"), D("1"))["pnl"] == D("-450")


@pytest.mark.parametrize(
    ("contracts", "multiplier"),
    [(2.5, D("1")), (True, D("1")), (1, D("0")), (1, D("1e19"))],
)
def test_pnl_rejects_invalid_contract_or_multiplier(contracts: object, multiplier: Decimal) -> None:
    with pytest.raises(DomainError):
        futures_pnl(contracts, multiplier, D("1"), D("2"))  # type: ignore[arg-type]


def test_decimal_context_isolated_from_caller_rounding_and_traps() -> None:
    caller = getcontext()
    original_rounding = caller.rounding
    original_traps = caller.traps.copy()
    try:
        caller.rounding = ROUND_DOWN
        for signal in original_traps:
            caller.traps[signal] = False
        assert futures_pnl(
            1,
            D("9"),
            D("0"),
            D("9999999999999999999999999999999999"),
        )["pnl"] == D("8.999999999999999999999999999999999E+34")
    finally:
        caller.rounding = original_rounding
        for signal, trapped in original_traps.items():
            caller.traps[signal] = trapped


@pytest.mark.parametrize("bad", [D("NaN"), D("Infinity"), D("1e19"), D("1e-19")])
def test_nonfinite_and_out_of_bound_decimal_rejected(bad: Decimal) -> None:
    with pytest.raises(DomainError):
        futures_pnl(1, D("1"), bad, D("1"))


def test_margin_maintenance_call_targets_initial_and_does_not_act() -> None:
    result = futures_margin(2, D("10"), D("-4"), D("100"), D("70"), D("120"))
    assert result["initial_margin_required"] == D("200")
    assert result["maintenance_margin_required"] == D("140")
    assert result["maintenance_breached"] is True
    assert result["margin_call_top_up_to_initial"] == D("80")
    assert result["action_taken"] is False
    assert (
        futures_margin(-2, D("10"), D("5"), D("100"), D("70"), D("150"))["maintenance_breached"]
        is False
    )


def test_margin_rejects_bad_rates_and_equity() -> None:
    with pytest.raises(DomainError):
        futures_margin(1, D("1"), D("2"), D("10"), D("11"), D("20"))
    with pytest.raises(DomainError):
        futures_margin(1, D("1"), D("2"), D("10"), D("5"), D("-1"))


def test_daily_settlement_telescopes_and_banks_variation_without_purchase_debit() -> None:
    result = futures_settlement(
        2, D("10"), (D("100"), D("103"), D("99")), D("500"), D("100"), D("70")
    )
    assert [row["variation"] for row in result["daily_settlements"]] == [  # type: ignore[index]
        D("0"),
        D("60"),
        D("-80"),
    ]
    assert result["cumulative_variation"] == D("-20")
    assert result["final_cash"] == D("480")
    assert result["notional_purchase_debit"] == D("0")


def test_short_settlement_negative_price_and_margin_call_are_not_topped_up() -> None:
    result = futures_settlement(-1, D("5"), (D("-2"), D("-10")), D("100"), D("80"), D("50"))
    day = result["daily_settlements"][1]  # type: ignore[index]
    assert day["variation"] == D("40")
    assert result["final_cash"] == D("140")
    assert day["action_taken"] is False
    assert not day["maintenance_breached"]

    breach = futures_settlement(1, D("1"), (D("10"), D("0")), D("100"), D("100"), D("95"))
    row = breach["daily_settlements"][1]  # type: ignore[index]
    assert row["maintenance_breached"] is True
    assert row["margin_call_top_up_to_initial"] == D("10")
    assert row["cash"] == D("90")
    assert breach["final_cash"] == D("90")


def test_settlement_requires_initial_collateral_and_bounded_tuple() -> None:
    with pytest.raises(DomainError):
        futures_settlement(2, D("1"), (D("1"),), D("19"), D("10"), D("5"))
    with pytest.raises(DomainError):
        futures_settlement(1, D("1"), [D("1")], D("10"), D("1"), D("1"))  # type: ignore[arg-type]
    with pytest.raises(DomainError):
        futures_settlement(1, D("1"), tuple(D("1") for _ in range(257)), D("10"), D("1"), D("1"))


def test_difference_continuous_contract_preserves_raw_and_backadjusts_history() -> None:
    result = continuous_contract(
        (D("100"), D("101"), D("103"), D("104")),
        ("OLD", "OLD", "NEW", "NEW"),
        (D("0"), D("0"), D("101"), D("0")),
    )
    assert result["raw_prices"] == (D("100"), D("101"), D("103"), D("104"))
    assert result["adjusted_prices"] == (D("102"), D("103"), D("103"), D("104"))
    roll = result["rolls"][0]  # type: ignore[index]
    assert roll["gap"] == D("2")
    assert result["tradable"] is False


def test_multiple_rolls_add_gaps_only_to_prior_history() -> None:
    result = continuous_contract(
        (D("50"), D("51"), D("53"), D("55")),
        ("A", "A", "B", "C"),
        (D("0"), D("0"), D("52"), D("54")),
    )
    assert result["adjusted_prices"] == (D("52"), D("53"), D("54"), D("55"))
    assert [roll["gap"] for roll in result["rolls"]] == [D("1"), D("1")]  # type: ignore[index]


def test_nonroll_rows_need_zero_old_quote_and_first_row_cannot_be_roll() -> None:
    with pytest.raises(DomainError):
        continuous_contract((D("1"),), ("A",), (D("1"),))
    with pytest.raises(DomainError):
        continuous_contract((D("1"), D("2")), ("A", "A"), (D("0"), D("1")))


def test_continuous_contract_rejects_unsupported_methods_lengths_and_unbounded_rows() -> None:
    with pytest.raises(DomainError):
        continuous_contract((D("1"),), ("A",), (D("0"),), method="ratio")
    with pytest.raises(DomainError):
        continuous_contract((D("1"),), ("A", "B"), (D("0"),))
    with pytest.raises(DomainError):
        continuous_contract(
            tuple(D("1") for _ in range(257)),
            tuple("A" for _ in range(257)),
            tuple(D("0") for _ in range(257)),
        )
