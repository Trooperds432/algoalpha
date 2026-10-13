"""Decimal-only European option pricing, terminal payoff, and equity margin research.

The normal CDF uses a deterministic Hastings polynomial approximation with a
documented absolute error below 7.5e-8. All financial inputs and calculations
remain Decimal; no float conversion is used. This module is a research model,
not an exchange margin calculator or broker adapter.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Literal

from quant_terminal.core.exceptions import DomainError

_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWO = Decimal("2")
_SQRT_TWO_PI = Decimal("2.506628274631000502415765284811045")
_CDF_COEFFICIENTS = (
    Decimal("0.319381530"),
    Decimal("-0.356563782"),
    Decimal("1.781477937"),
    Decimal("-1.821255978"),
    Decimal("1.330274429"),
)
_EUROPEAN = "european"
_EQUITY_MARGIN_POLICY = "equity_strategy_based"


def _context() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN)


def _decimal(value: object, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DomainError(f"{field} must be a finite Decimal")
    return value


def _positive(
    value: object,
    field: str,
    *,
    maximum: Decimal = Decimal("1e12"),
    minimum: Decimal = Decimal("1e-12"),
) -> Decimal:
    result = _decimal(value, field)
    if result < minimum or result > maximum:
        raise DomainError(f"{field} must be between {minimum} and {maximum}")
    return result


def _nonnegative(value: object, field: str, *, maximum: Decimal = Decimal("1e12")) -> Decimal:
    result = _decimal(value, field)
    if result < 0 or result > maximum:
        raise DomainError(f"{field} must be between zero and {maximum}")
    return result


def _option_type(value: object) -> Literal["call", "put"]:
    if not isinstance(value, str) or value.lower() not in ("call", "put"):
        raise DomainError("option_type must be 'call' or 'put'")
    return value.lower()  # type: ignore[return-value]


def _normal_pdf(value: Decimal) -> Decimal:
    return (-value * value / _TWO).exp() / _SQRT_TWO_PI


def _normal_cdf(value: Decimal) -> Decimal:
    """Hastings approximation to Phi(x), absolute error bounded by 7.5e-8."""
    if value == _ZERO:
        return Decimal("0.5")
    sign = _ONE if value > _ZERO else -_ONE
    x = abs(value)
    t = _ONE / (_ONE + Decimal("0.2316419") * x)
    polynomial = _ZERO
    power = t
    for coefficient in _CDF_COEFFICIENTS:
        polynomial += coefficient * power
        power *= t
    tail = _normal_pdf(x) * polynomial
    return _ONE - tail if sign > _ZERO else tail


def option_price(
    option_type: str,
    spot: Decimal,
    strike: Decimal,
    time_to_expiry: Decimal,
    volatility: Decimal,
    risk_free_rate: Decimal,
    dividend_yield: Decimal = _ZERO,
    style: str = _EUROPEAN,
) -> dict[str, Decimal | str]:
    """Price a European call/put and return annualized Black–Scholes Greeks.

    Greeks are per share: delta per $1 spot move; gamma per $1 squared;
    theta per year; vega per 1.00 absolute volatility change; rho per 1.00
    absolute rate change. The CDF approximation contributes at most 7.5e-8
    absolute probability error. Zero expiry and zero volatility are excluded
    because interior Greeks are undefined or discontinuous there; use
    :func:`option_payoff` for terminal value.
    """
    kind = _option_type(option_type)
    if not isinstance(style, str) or style.lower() != _EUROPEAN:
        raise DomainError("only European option style is supported")
    s = _positive(spot, "spot")
    k = _positive(strike, "strike")
    t = _positive(time_to_expiry, "time_to_expiry", maximum=Decimal("100"))
    sigma = _positive(volatility, "volatility", maximum=Decimal("5"))
    rate = _decimal(risk_free_rate, "risk_free_rate")
    dividend = _decimal(dividend_yield, "dividend_yield")
    if abs(rate) > _ONE or abs(dividend) > _ONE:
        raise DomainError("rates must have absolute value no greater than one")

    with localcontext(_context()):
        sqrt_t = t.sqrt()
        sigma_sqrt_t = sigma * sqrt_t
        discount_r = (-rate * t).exp()
        discount_q = (-dividend * t).exp()
        d1 = ((s / k).ln() + (rate - dividend + sigma * sigma / _TWO) * t) / sigma_sqrt_t
        d2 = d1 - sigma_sqrt_t
        n_d1 = _normal_cdf(d1)
        n_d2 = _normal_cdf(d2)
        n_minus_d1 = _normal_cdf(-d1)
        n_minus_d2 = _normal_cdf(-d2)
        pdf_d1 = _normal_pdf(d1)
        discounted_spot = s * discount_q
        discounted_strike = k * discount_r
        if kind == "call":
            price = discounted_spot * n_d1 - discounted_strike * n_d2
            delta = discount_q * n_d1
            theta = (
                -(discounted_spot * pdf_d1 * sigma) / (_TWO * sqrt_t)
                - rate * discounted_strike * n_d2
                + dividend * discounted_spot * n_d1
            )
            rho = k * t * discount_r * n_d2
        else:
            price = discounted_strike * n_minus_d2 - discounted_spot * n_minus_d1
            delta = discount_q * (n_d1 - _ONE)
            theta = (
                -(discounted_spot * pdf_d1 * sigma) / (_TWO * sqrt_t)
                + rate * discounted_strike * n_minus_d2
                - dividend * discounted_spot * n_minus_d1
            )
            rho = -k * t * discount_r * n_minus_d2
        gamma = discount_q * pdf_d1 / (s * sigma_sqrt_t)
        vega = discounted_spot * pdf_d1 * sqrt_t
        if price < _ZERO and abs(price) < Decimal("1e-30"):
            price = _ZERO
        if price < _ZERO:
            raise DomainError("model produced a negative option value")
        return {
            "option_type": kind,
            "style": _EUROPEAN,
            "price": +price,
            "delta": +delta,
            "gamma": +gamma,
            "theta_per_year": +theta,
            "vega_per_1_abs": +vega,
            "rho_per_1_abs": +rho,
            "cdf_absolute_error_bound": Decimal("7.5e-8"),
        }


def option_payoff(
    option_type: str,
    spot: Decimal,
    strike: Decimal,
    contracts: int,
    multiplier: int,
    premium: Decimal,
) -> dict[str, Decimal | int | str]:
    """Return signed terminal gross payoff and premium-adjusted PnL.

    Positive contract counts are long; negative counts are short. Premium is
    a per-underlying-unit amount paid by longs and received by shorts.
    """
    kind = _option_type(option_type)
    s = _nonnegative(spot, "spot")
    k = _positive(strike, "strike")
    premium_value = _decimal(premium, "premium")
    if premium_value < _ZERO or premium_value > Decimal("1e12"):
        raise DomainError("premium must be between zero and 1e12")
    _validate_contracts(contracts, multiplier)
    with localcontext(_context()):
        intrinsic = max(s - k, _ZERO) if kind == "call" else max(k - s, _ZERO)
        signed_units = Decimal(contracts * multiplier)
        gross = intrinsic * signed_units
        net = (intrinsic - premium_value) * signed_units
        return {
            "option_type": kind,
            "contracts": contracts,
            "multiplier": multiplier,
            "intrinsic_per_share": +intrinsic,
            "gross_payoff": +gross,
            "net_pnl": +net,
        }


def option_margin(
    option_type: str,
    spot: Decimal,
    strike: Decimal,
    premium: Decimal,
    contracts: int,
    multiplier: int,
    policy: str = _EQUITY_MARGIN_POLICY,
) -> dict[str, Decimal | int | str]:
    """Research estimate for long premium and uncovered short U.S. equity options.

    Short call requirement per share: premium + max(20% spot − OTM,
    10% spot). Short put: premium + max(20% spot − OTM, 10% strike).
    Long positions reserve premium paid. No portfolio offsets, covered calls,
    index-option rules, or broker-specific add-ons are modeled.
    """
    kind = _option_type(option_type)
    s = _positive(spot, "spot")
    k = _positive(strike, "strike")
    premium_value = _decimal(premium, "premium")
    if premium_value < _ZERO or premium_value > Decimal("1e12"):
        raise DomainError("premium must be between zero and 1e12")
    _validate_contracts(contracts, multiplier)
    if policy != _EQUITY_MARGIN_POLICY:
        raise DomainError("only equity_strategy_based margin policy is supported")
    with localcontext(_context()):
        if contracts >= 0:
            per_share = premium_value
            position = "long_premium"
        elif kind == "call":
            out_of_money = max(k - s, _ZERO)
            base = max(Decimal("0.20") * s - out_of_money, Decimal("0.10") * s)
            per_share = premium_value + base
            position = "uncovered_short_call"
        else:
            out_of_money = max(s - k, _ZERO)
            base = max(Decimal("0.20") * s - out_of_money, Decimal("0.10") * k)
            per_share = premium_value + base
            position = "uncovered_short_put"
        required = per_share * Decimal(abs(contracts) * multiplier)
        return {
            "policy": _EQUITY_MARGIN_POLICY,
            "position": position,
            "contracts": contracts,
            "multiplier": multiplier,
            "margin_per_share": +per_share,
            "required_margin": +required,
        }


def _validate_contracts(contracts: object, multiplier: object) -> None:
    if type(contracts) is not int or abs(contracts) > 1_000_000_000:
        raise DomainError("contracts must be a signed integer with absolute value at most 1e9")
    if type(multiplier) is not int or multiplier <= 0 or multiplier > 100000:
        raise DomainError("multiplier must be a positive integer no greater than 100000")
