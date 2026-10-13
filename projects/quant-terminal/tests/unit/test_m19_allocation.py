"""Independent analytic allocation fixtures and invalid-domain cases."""

from decimal import ROUND_UP, Decimal, Inexact, localcontext

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.portfolio_research.allocation import (
    black_litterman,
    mean_variance,
    risk_contributions,
    risk_parity,
    validate_covariance,
    volatility_target,
)

D = Decimal
DIAGONAL = ((D("0.04"), D(0)), (D(0), D("0.16")))
CORRELATED = ((D("0.04"), D("0.02")), (D("0.02"), D("0.16")))


def close(actual: Decimal, expected: Decimal, tolerance: str = "1e-28") -> None:
    assert abs(actual - expected) < D(tolerance)


def test_diagonal_volatility_target_and_leverage_cap() -> None:
    # Half of each asset: variance = .25*(.04+.16)=.05.
    with localcontext() as ctx:
        ctx.prec = 50
        multiplier = D("0.1") / D("0.05").sqrt()
        expected = D("0.5") * multiplier
    weights = volatility_target((D("0.5"), D("0.5")), DIAGONAL, D("0.1"))
    for weight in weights:
        close(weight, expected)
    capped = volatility_target((D("0.5"), D("0.5")), DIAGONAL, D(10), D("1.5"))
    assert capped == (D("0.75"), D("0.75"))
    short = volatility_target((D(-1), D(1)), DIAGONAL, D(10), D(1))
    assert short == (D("-0.5"), D("0.5"))


def test_euler_risk_contributions_sum_to_volatility() -> None:
    contributions = risk_contributions((D("0.5"), D("0.5")), DIAGONAL)
    with localcontext() as ctx:
        ctx.prec = 50
        volatility = D("0.05").sqrt()
        close(contributions[0], D("0.01") / volatility)
        close(contributions[1], D("0.04") / volatility)
        close(sum(contributions), volatility)


def test_hedging_position_can_have_negative_risk_contribution() -> None:
    contribution = risk_contributions((D(1), D("-0.1")), CORRELATED)
    assert contribution[0] > 0
    assert contribution[1] < 0


@pytest.mark.parametrize("covariance", [DIAGONAL, CORRELATED])
def test_two_asset_risk_parity_has_inverse_volatility_weights(covariance: object) -> None:
    # For two assets equal risk implies w1*sigma1=w2*sigma2 even with correlation.
    result = risk_parity(covariance)  # type: ignore[arg-type]
    with localcontext() as ctx:
        ctx.prec = 50
        close(result[0], D(2) / D(3), "1e-11")
        close(result[1], D(1) / D(3), "1e-11")
    assert all(w > 0 for w in result)
    close(sum(result), D(1))


def test_three_asset_correlated_risk_budgets() -> None:
    covariance = (
        (D("0.04"), D("0.01"), D("-0.005")),
        (D("0.01"), D("0.09"), D("0.02")),
        (D("-0.005"), D("0.02"), D("0.16")),
    )
    weights = risk_parity(covariance)
    contributions = risk_contributions(weights, covariance)
    total = sum(contributions)
    for contribution in contributions:
        close(contribution / total, D(1) / D(3), "1e-11")


def test_risk_parity_rejects_nonconvergence() -> None:
    with pytest.raises(DomainError, match="converge"):
        risk_parity(CORRELATED, tolerance=D("1e-18"), max_iterations=1)


def test_mean_variance_matches_hand_solved_first_order_condition() -> None:
    # mu=(.08,.12), diagonal Sigma=(.04,.16), gamma=2:
    # .08-.08*w1=.12-.32*w2, w1+w2=1 gives (.7,.3).
    result = mean_variance((D("0.08"), D("0.12")), DIAGONAL, D(2))
    close(result[0], D("0.7"))
    close(result[1], D("0.3"))
    close(sum(result), D(1))


def test_mean_variance_explicitly_permits_shorts() -> None:
    # .01-.04*w1=.5-.16*w2 -> w1=-1.65, w2=2.65.
    result = mean_variance((D("0.01"), D("0.5")), DIAGONAL)
    close(result[0], D("-1.65"))
    close(result[1], D("2.65"))


def test_black_litterman_absolute_view_analytic_precision_weighting() -> None:
    # pi=(.05,.2). tau*Sigma11=.002, Omega=.002 so posterior first
    # return averages prior .05 and view .09. Independent second unchanged.
    result = black_litterman(
        DIAGONAL,
        (D("0.5"), D("0.5")),
        ((D(1), D(0)),),
        (D("0.09"),),
        ((D("0.002"),),),
    )
    close(result[0], D("0.07"))
    close(result[1], D("0.2"))


def test_black_litterman_relative_view_and_multiple_view_dimensions() -> None:
    # tau=.1, gamma=1, pi=(.02,.08); P=(1,-1); innovation=.02-(-.06)=.08.
    # innovation variance .1*(.04+.16)+.02=.04; update coefficient =2.
    result = black_litterman(
        DIAGONAL,
        (D("0.5"), D("0.5")),
        ((D(1), D(-1)),),
        (D("0.02"),),
        ((D("0.02"),),),
        tau=D("0.1"),
        risk_aversion=D(1),
    )
    close(result[0], D("0.028"))
    close(result[1], D("0.048"))
    result = black_litterman(
        DIAGONAL,
        (D("0.5"), D("0.5")),
        ((D(1), D(0)), (D(0), D(1))),
        (D("0.09"), D("0.1")),
        ((D("0.002"), D(0)), (D(0), D("0.008"))),
    )
    close(result[0], D("0.07"))
    close(result[1], D("0.15"))


@pytest.mark.parametrize(
    "invalid",
    [
        (),
        ((D(1), D(0)),),
        ((D(1), D("0.1")), (D("0.2"), D(1))),
        ((D(1), D(1)), (D(1), D(1))),
        ((D("0.02"), D("0.04")), (D("0.04"), D("0.08"))),
        ((D(1), D(1)), (D(1), D("1.00000000000000000000000000000001"))),
        ((D(1), D(2)), (D(2), D(1))),
        ((D(-1),),),
        ((D("NaN"),),),
        ((D("Infinity"),),),
        ((1.0,),),
        ((D("1e1000000"),),),
        ((D("1.1234567890123456789012345678901234"),),),
    ],
)
def test_covariance_rejects_invalid_domains(invalid: object) -> None:
    with pytest.raises(DomainError):
        validate_covariance(invalid)  # type: ignore[arg-type]


def test_invalid_parameters_and_dimensions_fail_closed() -> None:
    for weights in [(D(0), D(0)), (D(1),), (D("NaN"), D(1))]:
        with pytest.raises(DomainError):
            risk_contributions(weights, DIAGONAL)
    for target in [D(0), D(-1), D("NaN")]:
        with pytest.raises(DomainError):
            volatility_target((D("0.5"), D("0.5")), DIAGONAL, target)
    with pytest.raises(DomainError):
        volatility_target((D(1), D(1)), DIAGONAL, D(1), D(0))
    for iterations in [0, True, 10001]:
        with pytest.raises(DomainError):
            risk_parity(DIAGONAL, max_iterations=iterations)
    for tolerance in [D(0), D(1), D("NaN")]:
        with pytest.raises(DomainError):
            risk_parity(DIAGONAL, tolerance=tolerance)
    with pytest.raises(DomainError):
        mean_variance((D("0.1"),), DIAGONAL)
    with pytest.raises(DomainError):
        mean_variance((D("0.1"), D("0.1")), DIAGONAL, D(0))


@pytest.mark.parametrize(
    "overrides",
    [
        {"market_weights": (D("0.4"), D("0.4"))},
        {"market_weights": (D(-1), D(2))},
        {"views": ((D(1),),)},
        {"views": ((D(0), D(0)),)},
        {"view_returns": (D(1), D(2))},
        {"view_covariance": ((D(0),),)},
        {"view_covariance": DIAGONAL},
        {"tau": D(0)},
        {"risk_aversion": D(-1)},
    ],
)
def test_black_litterman_rejects_invalid_domains(overrides: dict[str, object]) -> None:
    parameters: dict[str, object] = {
        "covariance": DIAGONAL,
        "market_weights": (D("0.5"), D("0.5")),
        "views": ((D(1), D(0)),),
        "view_returns": (D("0.1"),),
        "view_covariance": ((D("0.01"),),),
    }
    parameters.update(overrides)
    with pytest.raises(DomainError):
        black_litterman(**parameters)  # type: ignore[arg-type]


def test_context_is_local_and_research_does_not_mutate_inputs() -> None:
    baseline = (
        volatility_target((D("0.5"), D("0.5")), DIAGONAL, D("0.1")),
        risk_contributions((D("0.5"), D("0.5")), DIAGONAL),
        risk_parity(CORRELATED),
        mean_variance((D("0.08"), D("0.12")), DIAGONAL, D(2)),
        black_litterman(
            DIAGONAL,
            (D("0.5"), D("0.5")),
            ((D(1), D(-1)),),
            (D("0.02"),),
            ((D("0.02"),),),
        ),
    )
    with localcontext() as ctx:
        ctx.prec = 9
        ctx.rounding = ROUND_UP
        ctx.traps[Inexact] = True
        result = mean_variance((D("0.08"), D("0.12")), DIAGONAL, D(2))
        assert ctx.prec == 9
        assert ctx.rounding == ROUND_UP
        assert ctx.traps[Inexact]
        assert result == (D("0.7"), D("0.3"))
        assert DIAGONAL[0][0] == D("0.04")
        assert volatility_target((D("0.5"), D("0.5")), DIAGONAL, D("0.1")) == baseline[0]
        assert risk_contributions((D("0.5"), D("0.5")), DIAGONAL) == baseline[1]
        assert risk_parity(CORRELATED) == baseline[2]
        assert result == baseline[3]
        assert (
            black_litterman(
                DIAGONAL,
                (D("0.5"), D("0.5")),
                ((D(1), D(-1)),),
                (D("0.02"),),
                ((D("0.02"),),),
            )
            == baseline[4]
        )
