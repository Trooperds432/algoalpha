from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.portfolio_research.diagnostics import (
    concentration,
    correlation_regimes,
    factor_attribution,
    factor_covariance,
    factor_exposures,
    liquidity_capacity,
    stress_scenarios,
)

D = Decimal


def test_factor_golden_exposures_and_complete_attribution() -> None:
    weights = (D(".6"), D(".4"))
    loadings = ((D(1), D(2)), (D(".5"), D(-1)))
    assert factor_exposures(weights, loadings)["exposures"] == (D(".8"), D(".8"))
    result = factor_attribution(weights, loadings, (D(".1"), D(".05")), (D(".01"), D("-.02")))
    assert result == {
        "factor_contributions": (D(".08"), D(".04")),
        "residual_contribution": D("-.002"),
        "total_return": D(".118"),
    }


def test_factor_covariance_independent_golden_and_semidefinite() -> None:
    result = factor_covariance(
        ((D(1), D(2)), (D(0), D(1))),
        ((D(".04"), D(".01")), (D(".01"), D(".09"))),
        (D(".01"), D(".02")),
    )
    assert result["covariance"] == ((D(".45"), D(".19")), (D(".19"), D(".11")))
    assert factor_covariance(((D(1), D(1)),), ((D(1), D(1)), (D(1), D(1))), (D(0),)) == {
        "covariance": ((D(4),),)
    }
    assert factor_covariance(((D(1), D(1)),), ((D(0), D(0)), (D(0), D(1))), (D(0),)) == {
        "covariance": ((D(1),),)
    }


@pytest.mark.parametrize(
    "matrix",
    [
        ((D(1), D(2)), (D(2), D(1))),
        ((D(0), D(1)), (D(1), D(1))),
        ((D(1), D(0)), (D(1), D(1))),
        ((D(-1),),),
    ],
)
def test_factor_covariance_rejects_invalid_model(matrix: tuple[tuple[Decimal, ...], ...]) -> None:
    with pytest.raises(DomainError):
        factor_covariance(((D(1), D(1)),), matrix, (D(0),))


def test_stress_exact_nav_and_cash_zero_exposure() -> None:
    assert stress_scenarios(
        (D(".5"), D(".25")), ((D("-.2"), D("-.4")), (D(".2"), D(".4"))), D(1000)
    ) == {
        "scenario_returns": (D("-.2"), D(".2")),
        "nav_changes": (D(-200), D(200)),
        "nav_losses": (D(200), D(-200)),
    }
    assert stress_scenarios((D(-1),), ((D(2),),), D(100))["nav_losses"] == (D(200),)


def test_correlation_regime_sign_flip_and_unequal_windows() -> None:
    prior = ((D(-1), D(-2)), (D(0), D(0)), (D(1), D(2)))
    current = ((D(-1), D(2)), (D(1), D(-2)))
    result = correlation_regimes(prior, current, D(2))
    assert result["prior_correlation"] == ((D(1), D(1)), (D(1), D(1)))
    assert result["current_correlation"] == ((D(1), D(-1)), (D(-1), D(1)))
    assert result["max_absolute_change"] == D(2)
    assert result["regime"] == "changed"
    assert correlation_regimes(prior, prior)["regime"] == "stable"


def test_concentration_signed_leverage_cash_and_permutation() -> None:
    equal = concentration((D(".25"), D("-.25"), D(".25"), D(".25")))
    assert equal["hhi"] == D(".25")
    assert equal["effective_holdings"] == D(4)
    assert concentration((D(".2"), D(0)))["effective_holdings"] == D(1)
    assert concentration((D(2), D(-2)))["hhi"] == D(".5")
    assert concentration((D(".3"), D(".7")))["hhi"] == concentration((D(".7"), D(".3")))["hhi"]


def test_liquidity_golden_bottleneck_and_zero_weight() -> None:
    result = liquidity_capacity(
        (D(100), D(-300), D(0)), (D(1000), D(1000), D(1)), (D(".25"), D(".75"), D(0)), D(".1")
    )
    assert result["asset_liquidation_days"] == (D(1), D(3), D(0))
    assert result["portfolio_liquidation_days"] == D(3)
    with localcontext() as ctx:
        ctx.prec = 34
        assert result["daily_portfolio_capacity"] == D(100) / D(".75")
    assert result["bottleneck_asset_index"] == 1


@pytest.mark.parametrize(
    "invalid",
    [
        D("NaN"),
        D("Infinity"),
        D("1e-1000000"),
        D("1e1000000"),
        D("123456789012345678901234567890123456789"),
        1,
        True,
    ],
)
def test_input_precision_type_and_finiteness_rejection(invalid: object) -> None:
    with pytest.raises(DomainError):
        concentration([invalid])  # type: ignore[list-item]


@pytest.mark.parametrize(
    "operation",
    [
        lambda: factor_exposures((D(1), D(1)), ((D(1),),)),
        lambda: factor_exposures((D(1),), ((D(1),), (D(1), D(2)))),
        lambda: factor_attribution((D(1),), ((D(1),),), (D(1), D(2)), (D(0),)),
        lambda: factor_attribution((D(1),), ((D(1),),), (D(1),), (D(0), D(0))),
        lambda: factor_covariance(((D(1),),), ((D(1),),), (D(-1),)),
        lambda: stress_scenarios((D(1),), ((D("-1.1"),),), D(100)),
        lambda: stress_scenarios((D(1),), ((D(0),),), D(0)),
        lambda: stress_scenarios((D(1),), ((D(0), D(0)),), D(100)),
        lambda: correlation_regimes(((D(1),),), ((D(1),),)),
        lambda: correlation_regimes(((D(1),), (D(1),)), ((D(1),), (D(2),))),
        lambda: correlation_regimes(((D(1),), (D(2),)), ((D(1), D(1)), (D(2), D(2)))),
        lambda: correlation_regimes(((D(1),), (D(2),)), ((D(1),), (D(2),)), D(0)),
        lambda: concentration((D(0), D(0))),
        lambda: concentration(()),
        lambda: concentration((D(1),) * 65),
        lambda: liquidity_capacity((D(1),), (D(0),), (D(1),), D(".1")),
        lambda: liquidity_capacity((D(1),), (D(10),), (D(0),), D(".1")),
        lambda: liquidity_capacity((D(1),), (D(10),), (D(1),), D("1.1")),
        lambda: liquidity_capacity((D(1), D(2)), (D(10),), (D(1),), D(".1")),
    ],
)
def test_dimension_and_domain_rejections(operation: object) -> None:
    with pytest.raises(DomainError):
        operation()  # type: ignore[operator]


def test_decimal_context_does_not_change_results() -> None:
    with localcontext() as ctx:
        ctx.prec = 6
        low = concentration((D(1), D(2), D(3)))
    with localcontext() as ctx:
        ctx.prec = 60
        high = concentration((D(1), D(2), D(3)))
    assert low == high


def test_ambient_rounding_and_traps_do_not_change_result() -> None:
    normal = concentration((D(1), D(2), D(3)))
    with localcontext() as ctx:
        ctx.rounding = ROUND_DOWN
        ctx.traps[Inexact] = True
        assert concentration((D(1), D(2), D(3))) == normal


def test_factor_covariance_quadratic_form_and_asset_permutation() -> None:
    first = factor_covariance(((D(1),), (D(-2),)), ((D(".5"),),), (D(".1"), D(".2")))
    swapped = factor_covariance(((D(-2),), (D(1),)), ((D(".5"),),), (D(".2"), D(".1")))
    assert first["covariance"] == ((D(".6"), D(-1)), (D(-1), D("2.2")))
    assert swapped["covariance"] == ((D("2.2"), D(-1)), (D(-1), D(".6")))
    # Equal positions have variance .6 - 2 + 2.2 = .8, above zero.
    assert D(".6") - D(2) + D("2.2") == D(".8")
