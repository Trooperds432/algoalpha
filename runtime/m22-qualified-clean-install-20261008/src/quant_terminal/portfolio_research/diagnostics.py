"""Bounded Decimal portfolio diagnostics; caller supplies already aligned research data.

Weights are signed fractions of NAV. Unspecified residual cash earns zero in factor
attribution and stress scenarios. Concentration excludes cash and normalizes gross
asset weights; liquidity capacity is a one-day participation budget, not an alpha
or executable-order claim. Correlation windows contain rows of aligned observations.
"""

from collections.abc import Sequence
from decimal import Context, Decimal, localcontext

from quant_terminal.core.exceptions import DomainError

type Vector = Sequence[Decimal]
type Matrix = Sequence[Vector]
type Result = dict[str, object]
ZERO = Decimal(0)
ONE = Decimal(1)


def _decimal(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DomainError(f"{name} must be a finite Decimal")
    parts = value.as_tuple()
    if len(parts.digits) > 38 or not -18 <= int(parts.exponent) <= 18 or value.copy_abs() > 10**18:
        raise DomainError(f"{name} exceeds supported precision or magnitude")
    return value


def _vector(values: Vector, name: str, limit: int = 64) -> tuple[Decimal, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= limit:
        raise DomainError(f"{name} must be a nonempty bounded list or tuple")
    return tuple(_decimal(value, name) for value in values)


def _matrix(values: Matrix, name: str, row_limit: int = 64) -> tuple[tuple[Decimal, ...], ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= row_limit:
        raise DomainError(f"{name} must be a nonempty bounded matrix")
    rows = tuple(_vector(row, name) for row in values)
    if any(len(row) != len(rows[0]) for row in rows):
        raise DomainError(f"{name} must be rectangular")
    return rows


def _aligned(weights: Vector, loadings: Matrix) -> tuple[tuple[Decimal, ...], ...]:
    rows = _matrix(loadings, "loadings")
    if len(_vector(weights, "weights")) != len(rows):
        raise DomainError("weights and loadings must have the same asset dimension")
    return rows


def factor_exposures(weights: Vector, loadings: Matrix) -> Result:
    """Return signed factor exposures: portfolio weights times asset factor loadings."""
    rows = _aligned(weights, loadings)
    with localcontext(Context(prec=34)):
        exposures = tuple(
            sum((w * row[j] for w, row in zip(weights, rows, strict=True)), ZERO)
            for j in range(len(rows[0]))
        )
    return {"exposures": exposures}


def factor_attribution(
    weights: Vector, loadings: Matrix, factor_returns: Vector, residual_returns: Vector
) -> Result:
    """Additive single-period simple-return attribution, including explicit residuals."""
    rows = _aligned(weights, loadings)
    factors = _vector(factor_returns, "factor_returns")
    residuals = _vector(residual_returns, "residual_returns")
    if len(factors) != len(rows[0]) or len(residuals) != len(rows):
        raise DomainError("factor or residual dimensions do not align")
    with localcontext(Context(prec=34)):
        exposure = tuple(
            sum((w * row[j] for w, row in zip(weights, rows, strict=True)), ZERO)
            for j in range(len(factors))
        )
        contributions = tuple(e * r for e, r in zip(exposure, factors, strict=True))
        residual = sum((w * r for w, r in zip(weights, residuals, strict=True)), ZERO)
        total = sum(contributions, ZERO) + residual
    return {
        "factor_contributions": contributions,
        "residual_contribution": residual,
        "total_return": total,
    }


def _covariance(matrix: Matrix) -> tuple[tuple[Decimal, ...], ...]:
    rows = _matrix(matrix, "factor_covariance")
    n = len(rows)
    if len(rows[0]) != n or any(rows[i][j] != rows[j][i] for i in range(n) for j in range(n)):
        raise DomainError("factor covariance must be square and symmetric")
    # LDL decomposition with exact zero-pivot rules permits positive semidefinite matrices.
    work = [list(row) for row in rows]
    for k in range(n):
        pivot = work[k][k]
        if pivot < ZERO:
            raise DomainError("factor covariance must be positive semidefinite")
        if pivot == ZERO:
            if any(work[i][k] != ZERO for i in range(k + 1, n)):
                raise DomainError("factor covariance must be positive semidefinite")
            continue
        for i in range(k + 1, n):
            for j in range(i, n):
                value = work[i][j] - work[i][k] * work[j][k] / pivot
                work[i][j] = work[j][i] = value
    return rows


def factor_covariance(
    loadings: Matrix, factor_covariance_matrix: Matrix, idiosyncratic_variances: Vector
) -> Result:
    """Compute B F B-transpose plus independent asset-specific variance diagonal."""
    rows = _matrix(loadings, "loadings")
    residual = _vector(idiosyncratic_variances, "idiosyncratic_variances")
    with localcontext(Context(prec=34)):
        factors = _covariance(factor_covariance_matrix)
        if len(factors) != len(rows[0]) or len(residual) != len(rows):
            raise DomainError("factor covariance or variance dimensions do not align")
        if any(v < ZERO for v in residual):
            raise DomainError("idiosyncratic variance cannot be negative")
        covariance = tuple(
            tuple(
                sum(
                    (
                        rows[i][a] * factors[a][b] * rows[j][b]
                        for a in range(len(factors))
                        for b in range(len(factors))
                    ),
                    ZERO,
                )
                + (residual[i] if i == j else ZERO)
                for j in range(len(rows))
            )
            for i in range(len(rows))
        )
    return {"covariance": covariance}


def stress_scenarios(weights: Vector, asset_shocks: Matrix, nav: Decimal) -> Result:
    """Return signed NAV changes/losses for aligned asset simple-return scenario rows."""
    w = _vector(weights, "weights")
    shocks = _matrix(asset_shocks, "asset_shocks")
    capital = _decimal(nav, "nav")
    if capital <= ZERO or len(shocks[0]) != len(w):
        raise DomainError("positive NAV and asset-aligned shocks required")
    if any(shock < -ONE for row in shocks for shock in row):
        raise DomainError("asset simple return shock cannot be below -1")
    with localcontext(Context(prec=34)):
        returns = tuple(
            sum((weight * shock for weight, shock in zip(w, row, strict=True)), ZERO)
            for row in shocks
        )
        changes = tuple(capital * r for r in returns)
    return {
        "scenario_returns": returns,
        "nav_changes": changes,
        "nav_losses": tuple(v.copy_negate() for v in changes),
    }


def _correlations(window: Matrix) -> tuple[tuple[Decimal, ...], ...]:
    rows = _matrix(window, "return_window", 4096)
    if len(rows) < 2:
        raise DomainError("correlation requires at least two observations")
    n = len(rows[0])
    means = tuple(sum((row[j] for row in rows), ZERO) / len(rows) for j in range(n))
    centered = tuple(tuple(row[j] - means[j] for j in range(n)) for row in rows)
    variances = tuple(sum((row[j] ** 2 for row in centered), ZERO) for j in range(n))
    if any(v == ZERO for v in variances):
        raise DomainError("correlation undefined for a zero-variance asset")
    return tuple(
        tuple(
            ONE
            if i == j
            else max(
                -ONE,
                min(
                    ONE,
                    sum((row[i] * row[j] for row in centered), ZERO)
                    / (variances[i] * variances[j]).sqrt(),
                ),
            )
            for j in range(n)
        )
        for i in range(n)
    )


def correlation_regimes(
    prior_returns: Matrix, current_returns: Matrix, threshold: Decimal = Decimal("0.2")
) -> Result:
    """Classify regime change when maximum absolute pair correlation change >= threshold.

    This descriptive comparison is not a significance test. Row ordering/alignment
    is the caller's responsibility; differing observation counts are supported.
    """
    cutoff = _decimal(threshold, "threshold")
    if not ZERO < cutoff <= 2:
        raise DomainError("threshold must be in (0, 2]")
    with localcontext(Context(prec=34)):
        prior = _correlations(prior_returns)
        current = _correlations(current_returns)
        if len(prior) != len(current):
            raise DomainError("correlation windows must share asset dimension")
        delta = max(
            (abs(current[i][j] - prior[i][j]) for i in range(len(prior)) for j in range(i)),
            default=ZERO,
        )
    return {
        "prior_correlation": prior,
        "current_correlation": current,
        "max_absolute_change": delta,
        "threshold": cutoff,
        "regime": "changed" if delta >= cutoff else "stable",
    }


def concentration(weights: Vector) -> Result:
    """Gross-asset-normalized HHI and inverse HHI; residual cash is excluded."""
    values = _vector(weights, "weights")
    with localcontext(Context(prec=34)):
        gross = sum((abs(v) for v in values), ZERO)
        if gross == ZERO:
            raise DomainError("concentration undefined for zero gross asset weight")
        normalized = tuple(abs(v) / gross for v in values)
        hhi = sum((v * v for v in normalized), ZERO)
        effective = ONE / hhi
    return {
        "gross_weight": gross,
        "normalized_absolute_weights": normalized,
        "hhi": hhi,
        "effective_holdings": effective,
    }


def liquidity_capacity(
    position_values: Vector, adv_values: Vector, weights: Vector, participation_limit: Decimal
) -> Result:
    """Aligned dollar positions and ADV give liquidation days and daily NAV capacity.

    ADV must be positive for every supplied asset. Zero portfolio weights do not
    constrain capacity; at least one nonzero weight is required. Shorts consume
    the same participation budget as longs. No netting across assets is assumed.
    """
    positions = _vector(position_values, "position_values")
    adv = _vector(adv_values, "adv_values")
    w = _vector(weights, "weights")
    participation = _decimal(participation_limit, "participation_limit")
    if len(positions) != len(adv) or len(w) != len(adv):
        raise DomainError("position, ADV and weight dimensions must align")
    if not ZERO < participation <= ONE or any(v <= ZERO for v in adv):
        raise DomainError("positive ADV and participation in (0, 1] required")
    if not any(w):
        raise DomainError("capacity undefined for zero gross weights")
    with localcontext(Context(prec=34)):
        budgets = tuple(v * participation for v in adv)
        days = tuple(abs(p) / b for p, b in zip(positions, budgets, strict=True))
        capacities = tuple(
            (b / abs(weight), i)
            for i, (b, weight) in enumerate(zip(budgets, w, strict=True))
            if weight != ZERO
        )
        capacity, bottleneck = min(capacities)
    return {
        "daily_dollar_budgets": budgets,
        "asset_liquidation_days": days,
        "portfolio_liquidation_days": max(days),
        "daily_portfolio_capacity": capacity,
        "bottleneck_asset_index": bottleneck,
    }
