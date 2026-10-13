"""Annual covariance-based allocations; these are research, not trade intents."""

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from quant_terminal.core.exceptions import DomainError
from quant_terminal.portfolio_research.math import (
    DECIMAL_PRECISION,
    ONE,
    ZERO,
    Matrix,
    Vector,
    cholesky,
    decimal_value,
    dot,
    matrix,
    matvec,
    quadratic,
    solve,
    vector,
)


def validate_covariance(covariance: Matrix) -> None:
    """Require finite bounded exact inputs, symmetry and strict PD."""
    cholesky(covariance)


def _portfolio(weights: Vector, covariance: Matrix) -> None:
    validate_covariance(covariance)
    vector(weights, "weights", len(covariance))
    if not any(weights):
        raise DomainError("portfolio weights cannot all be zero")


def volatility_target(
    weights: Vector,
    covariance: Matrix,
    target: Decimal,
    max_leverage: Decimal = Decimal(2),
) -> Vector:
    """Scale weights to annual volatility, limited by absolute gross leverage.

    If the leverage cap binds the target remains unattained. Cash is implicit;
    input and output weights need not sum to one.
    """
    _portfolio(weights, covariance)
    decimal_value(target, "target")
    decimal_value(max_leverage, "max_leverage")
    if target <= ZERO or max_leverage <= ZERO:
        raise DomainError("target and max_leverage must be positive")
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        volatility = quadratic(weights, covariance).sqrt()
        gross = sum((abs(weight) for weight in weights), ZERO)
        scale = min(target / volatility, max_leverage / gross)
        return tuple(weight * scale for weight in weights)


def risk_contributions(weights: Vector, covariance: Matrix) -> Vector:
    """Euler contributions to annual portfolio volatility, including shorts."""
    _portfolio(weights, covariance)
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        marginal = matvec(covariance, weights)
        volatility = dot(weights, marginal).sqrt()
        return tuple(w * m / volatility for w, m in zip(weights, marginal, strict=True))


def risk_parity(
    covariance: Matrix,
    tolerance: Decimal = Decimal("1e-12"),
    max_iterations: int = 1000,
) -> Vector:
    """Long-only equal variance budgets via cyclic coordinate minimisation.

    Converge when every normalised variance contribution is within tolerance
    of 1/n. The strictly convex log-barrier problem has a unique positive
    solution for PD covariance. Unsupported nonconvergence fails explicitly.
    """
    validate_covariance(covariance)
    decimal_value(tolerance, "tolerance")
    if not ZERO < tolerance < ONE:
        raise DomainError("tolerance must be between zero and one")
    if type(max_iterations) is not int or not 1 <= max_iterations <= 10000:
        raise DomainError("max_iterations must be an integer from 1 to 10000")
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        count = len(covariance)
        x = [ONE / covariance[i][i].sqrt() for i in range(count)]
        budget = ONE / Decimal(count)
        for _ in range(max_iterations):
            for i in range(count):
                off_diagonal = sum((covariance[i][j] * x[j] for j in range(count) if j != i), ZERO)
                root = (off_diagonal * off_diagonal + Decimal(4) * covariance[i][i]).sqrt()
                # Avoid subtractive cancellation for positive off-diagonal sums.
                x[i] = (
                    Decimal(2) / (root + off_diagonal)
                    if off_diagonal >= ZERO
                    else (root - off_diagonal) / (Decimal(2) * covariance[i][i])
                )
            total = sum(x, ZERO)
            weights = tuple(value / total for value in x)
            marginal = matvec(covariance, weights)
            variance = dot(weights, marginal)
            errors = tuple(abs(weights[i] * marginal[i] / variance - budget) for i in range(count))
            if max(errors) <= tolerance:
                return weights
        raise DomainError("risk parity failed to converge within max_iterations")


def mean_variance(
    expected_returns: Vector,
    covariance: Matrix,
    risk_aversion: Decimal = ONE,
) -> Vector:
    """Maximise mu'w - gamma/2 w'Sigma w, subject only to sum(w)=1.

    This unconstrained research solution explicitly permits short positions;
    it supplies neither gross leverage limits nor execution eligibility.
    """
    validate_covariance(covariance)
    vector(expected_returns, "expected_returns", len(covariance))
    decimal_value(risk_aversion, "risk_aversion")
    if risk_aversion <= ZERO:
        raise DomainError("risk_aversion must be positive")
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        ones = tuple(ONE for _ in covariance)
        inverse_mu = solve(covariance, expected_returns)
        inverse_one = solve(covariance, ones)
        lagrange = (sum(inverse_mu, ZERO) - risk_aversion) / sum(inverse_one, ZERO)
        return tuple(
            (m - lagrange * o) / risk_aversion for m, o in zip(inverse_mu, inverse_one, strict=True)
        )


def black_litterman(
    covariance: Matrix,
    market_weights: Vector,
    views: Matrix,
    view_returns: Vector,
    view_covariance: Matrix,
    tau: Decimal = Decimal("0.05"),
    risk_aversion: Decimal = Decimal("2.5"),
) -> Vector:
    """Posterior expected excess returns from equilibrium and Gaussian views.

    Covariance, Q and Omega must use consistent annualised units. Market weights
    are fully invested, long-only. At least one explicit view is required.
    """
    validate_covariance(covariance)
    vector(market_weights, "market_weights", len(covariance))
    matrix(views, "views")
    if len(views[0]) != len(covariance):
        raise DomainError("views dimension mismatch")
    if any(not any(row) for row in views):
        raise DomainError("each view must have a nonzero exposure")
    vector(view_returns, "view_returns", len(views))
    validate_covariance(view_covariance)
    if len(view_covariance) != len(views):
        raise DomainError("view covariance dimension mismatch")
    decimal_value(tau, "tau")
    decimal_value(risk_aversion, "risk_aversion")
    if tau <= ZERO or risk_aversion <= ZERO:
        raise DomainError("tau and risk_aversion must be positive")
    with localcontext(Context(prec=DECIMAL_PRECISION, rounding=ROUND_HALF_EVEN)):
        if any(w < ZERO for w in market_weights) or sum(market_weights, ZERO) != ONE:
            raise DomainError("market weights must be nonnegative and sum to one")
        prior = tuple(risk_aversion * value for value in matvec(covariance, market_weights))
        projected = tuple(matvec(covariance, row) for row in views)
        system = tuple(
            tuple(
                tau * dot(views[min(i, j)], projected[max(i, j)]) + view_covariance[i][j]
                for j in range(len(views))
            )
            for i in range(len(views))
        )
        innovation = tuple(q - p for q, p in zip(view_returns, matvec(views, prior), strict=True))
        update = solve(system, innovation, check_bounds=False)
        return tuple(
            prior[i] + tau * sum((projected[j][i] * update[j] for j in range(len(views))), ZERO)
            for i in range(len(covariance))
        )
