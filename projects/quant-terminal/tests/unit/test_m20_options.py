from __future__ import annotations

from decimal import Decimal

import pytest

from quant_terminal.advanced_assets.options import (
    _normal_cdf,
    option_margin,
    option_payoff,
    option_price,
)
from quant_terminal.core.exceptions import DomainError

D = Decimal
_SPOT_100 = D("100")
_EXPIRY_1 = D("1")
_VOL_020 = D("0.2")
_RATE_005 = D("0.05")


def decimal_value(value: Decimal | int | str) -> Decimal:
    return value if isinstance(value, Decimal) else D(value)


def bs(
    option_type: str,
    *,
    spot: Decimal = _SPOT_100,
    expiry: Decimal = _EXPIRY_1,
    vol: Decimal = _VOL_020,
    rate: Decimal = _RATE_005,
) -> dict[str, Decimal | str]:
    return option_price(
        option_type,
        spot,
        D("100"),
        expiry,
        vol,
        rate,
    )


@pytest.mark.parametrize(
    ("option_type", "expected"),
    [("call", D("10.45058357")), ("put", D("5.57352602"))],
)
def test_black_scholes_published_atm_fixture(option_type: str, expected: Decimal) -> None:
    result = bs(option_type)
    # CDF error is amplified by discounted spot and strike in the option value.
    assert abs(decimal_value(result["price"]) - expected) < D("0.00002")
    assert result["cdf_absolute_error_bound"] == D("7.5e-8")


def test_normal_cdf_approximation_respects_documented_absolute_tolerance() -> None:
    assert abs(_normal_cdf(D("0.35")) - D("0.636830651175619")) < D("7.5e-8")
    assert abs(_normal_cdf(D("-1")) - D("0.158655253931457")) < D("7.5e-8")
    assert _normal_cdf(D("0")) == D("0.5")


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_greeks_match_finite_differences(option_type: str) -> None:
    base = bs(option_type)
    bump = D("0.001")
    up_s = decimal_value(bs(option_type, spot=D("100") + bump)["price"])
    down_s = decimal_value(bs(option_type, spot=D("100") - bump)["price"])
    base_price = decimal_value(base["price"])
    delta_fd = (up_s - down_s) / (D("2") * bump)
    gamma_fd = (up_s - D("2") * base_price + down_s) / (bump * bump)
    vol_bump = D("0.0001")
    up_vol = decimal_value(bs(option_type, vol=D("0.2") + vol_bump)["price"])
    down_vol = decimal_value(bs(option_type, vol=D("0.2") - vol_bump)["price"])
    vega_fd = (up_vol - down_vol) / (D("2") * vol_bump)
    time_bump = D("0.0001")
    shorter = decimal_value(bs(option_type, expiry=D("1") - time_bump)["price"])
    longer = decimal_value(bs(option_type, expiry=D("1") + time_bump)["price"])
    theta_fd = (shorter - longer) / (D("2") * time_bump)
    rate_bump = D("0.0001")
    higher_rate = decimal_value(bs(option_type, rate=D("0.05") + rate_bump)["price"])
    lower_rate = decimal_value(bs(option_type, rate=D("0.05") - rate_bump)["price"])
    rho_fd = (higher_rate - lower_rate) / (D("2") * rate_bump)
    assert abs(decimal_value(base["delta"]) - delta_fd) < D("0.00001")
    assert abs(decimal_value(base["gamma"]) - gamma_fd) < D("0.0001")
    assert abs(decimal_value(base["vega_per_1_abs"]) - vega_fd) < D("0.001")
    assert abs(decimal_value(base["theta_per_year"]) - theta_fd) < D("0.001")
    assert abs(decimal_value(base["rho_per_1_abs"]) - rho_fd) < D("0.001")


def test_put_call_parity_with_dividend_yield() -> None:
    args = (D("123"), D("120"), D("0.75"), D("0.31"), D("0.04"))
    call = decimal_value(option_price("call", *args, dividend_yield=D("0.015"))["price"])
    put = decimal_value(option_price("put", *args, dividend_yield=D("0.015"))["price"])
    spot, strike, expiry, _vol, rate = args
    parity = spot * (-D("0.015") * expiry).exp() - strike * (-rate * expiry).exp()
    assert abs((call - put) - parity) < D("1e-25")


def test_option_payoff_applies_signed_contracts_and_premium() -> None:
    long_call = option_payoff("call", D("110"), D("100"), 2, 100, D("5"))
    assert long_call["gross_payoff"] == D("2000")
    assert long_call["net_pnl"] == D("1000")
    short_call = option_payoff("call", D("110"), D("100"), -2, 100, D("5"))
    assert short_call["gross_payoff"] == D("-2000")
    assert short_call["net_pnl"] == D("-1000")
    assert option_payoff("put", D("90"), D("100"), 1, 100, D("2"))["net_pnl"] == D("800")


@pytest.mark.parametrize(
    ("option_type", "contracts", "gross", "net"),
    [
        ("call", 1, D("0"), D("-100")),
        ("call", -1, D("0"), D("100")),
        ("put", 1, D("10000"), D("9900")),
        ("put", -1, D("-10000"), D("-9900")),
    ],
)
def test_option_payoff_allows_zero_terminal_spot(
    option_type: str, contracts: int, gross: Decimal, net: Decimal
) -> None:
    result = option_payoff(option_type, D("0"), D("100"), contracts, 100, D("1"))
    assert result["gross_payoff"] == gross
    assert result["net_pnl"] == net


def test_equity_strategy_based_margin_examples() -> None:
    short_call = option_margin("call", D("100"), D("110"), D("2"), -1, 100)
    assert short_call["margin_per_share"] == D("12")
    assert short_call["required_margin"] == D("1200")
    short_put = option_margin("put", D("110"), D("100"), D("1"), -1, 100)
    assert short_put["margin_per_share"] == D("13")
    assert short_put["required_margin"] == D("1300")
    long = option_margin("call", D("100"), D("110"), D("2"), 1, 100)
    assert long["position"] == "long_premium"
    assert long["required_margin"] == D("200")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"option_type": "binary"},
        {"style": "american"},
        {"time_to_expiry": D("0")},
        {"volatility": D("0")},
        {"spot": D("0")},
        {"risk_free_rate": D("NaN")},
        {"spot": 100.0},
    ],
)
def test_option_price_rejects_unsupported_or_invalid_inputs(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "option_type": "call",
        "spot": D("100"),
        "strike": D("100"),
        "time_to_expiry": D("1"),
        "volatility": D("0.2"),
        "risk_free_rate": D("0.05"),
        "dividend_yield": D("0"),
        "style": "european",
    }
    values.update(kwargs)
    with pytest.raises(DomainError):
        option_price(**values)  # type: ignore[arg-type]


def test_payoff_and_margin_reject_unsupported_shapes() -> None:
    with pytest.raises(DomainError, match="contracts"):
        option_payoff("call", D("100"), D("100"), 1.5, 100, D("1"))  # type: ignore[arg-type]
    with pytest.raises(DomainError, match="policy"):
        option_margin("call", D("100"), D("100"), D("1"), -1, 100, "portfolio-offset")
    with pytest.raises(DomainError, match="multiplier"):
        option_margin("put", D("100"), D("100"), D("1"), 1, 0)
