"""Versioned SHA-256 sampling and Decimal path metrics for Monte Carlo."""

from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from quant_terminal.core.exceptions import DomainError

from .models import (
    BootstrapMethod,
    MetricDistribution,
    MonteCarloPath,
    MonteCarloResult,
    MonteCarloSpec,
    metric_distribution,
)


def _context() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN)


def _index(spec: MonteCarloSpec, path: int, draw: int, upper: int) -> int:
    if upper <= 0:
        raise DomainError("Sampling range must be positive")
    bits = max(1, (upper - 1).bit_length())
    byte_count = (bits + 7) // 8
    mask = (1 << bits) - 1
    cutoff = (1 << bits) - ((1 << bits) % upper)
    counter = 0
    while True:
        token = f"algoalpha-monte-carlo-v1:{spec.fingerprint}:{spec.seed}:{path}:{draw}:{counter}"
        value = int.from_bytes(hashlib.sha256(token.encode("ascii")).digest()[:byte_count], "big")
        value &= mask
        if value < cutoff:
            return value % upper
        counter += 1


def _indices(spec: MonteCarloSpec, path: int) -> tuple[int, ...]:
    if spec.method in (BootstrapMethod.TRADE, BootstrapMethod.IID_RETURN):
        return tuple(
            _index(spec, path, draw, len(spec.returns)) for draw in range(spec.path_length)
        )
    assert spec.block_length is not None
    block_length = spec.block_length
    candidates = tuple(
        start
        for start in range(len(spec.returns) - block_length + 1)
        if len(set(spec.segment_ids[start : start + block_length])) == 1
    )
    indexes: list[int] = []
    draw = 0
    while len(indexes) < spec.path_length:
        start = candidates[_index(spec, path, draw, len(candidates))]
        take = min(block_length, spec.path_length - len(indexes))
        indexes.extend(range(start, start + take))
        draw += 1
    return tuple(indexes)


def _expected_shortfall(
    returns: tuple[Decimal, ...], confidence: Decimal
) -> tuple[Decimal | None, str | None]:
    losses = sorted((-value for value in returns), reverse=True)
    if len(losses) < 20 or Decimal(len(losses)) * (Decimal(1) - confidence) < 5:
        return None, "CVaR95 requires 20 observations and 5 expected tail observations"
    mass = (Decimal(1) - confidence) * Decimal(len(losses))
    whole = int(mass)
    fraction = mass - Decimal(whole)
    if whole == 0:
        return losses[0], None
    total = sum(losses[:whole], Decimal(0))
    if fraction and whole < len(losses):
        total += losses[whole] * fraction
    return total / mass, None


def _path(spec: MonteCarloSpec, index: int, sample_indices: tuple[int, ...]) -> MonteCarloPath:
    returns = tuple(spec.returns[i] for i in sample_indices)
    equity = [spec.initial_equity]
    solvent = True
    with localcontext(_context()):
        for value in returns:
            next_equity = equity[-1] * (Decimal(1) + value)
            equity.append(next_equity)
            if next_equity <= 0:
                solvent = False
        peak = equity[0]
        peak_index = 0
        active: int | None = None
        maximum_drawdown = Decimal(0)
        longest_duration = 0
        for point_index, point_equity in enumerate(equity):
            if point_equity >= peak:
                if active is not None:
                    longest_duration = max(longest_duration, point_index - active)
                    active = None
                peak = point_equity
                peak_index = point_index
                continue
            if active is None:
                active = peak_index
            if peak > 0:
                maximum_drawdown = max(maximum_drawdown, Decimal(1) - point_equity / peak)
        if active is not None:
            longest_duration = max(longest_duration, len(equity) - 1 - active)
        terminal = equity[-1]
        periods_per_year = (
            spec.trade_frequency_per_year
            if spec.method is BootstrapMethod.TRADE
            else Decimal(spec.periods_per_year or 0)
        )
        assert periods_per_year is not None
        years = Decimal(len(returns)) / periods_per_year
        cagr = (
            (terminal / spec.initial_equity).ln().__truediv__(years).exp() - 1
            if terminal > 0 and solvent and years > 0
            else None
        )
        mean = sum(returns, Decimal(0)) / Decimal(len(returns))
        variance = (
            sum(((value - mean) ** 2 for value in returns), Decimal(0)) / Decimal(len(returns) - 1)
            if len(returns) > 1
            else Decimal(0)
        )
        deviation = variance.sqrt()
        risk_free = ((Decimal(1) + spec.annual_risk_free_rate).ln() / periods_per_year).exp() - 1
        sharpe = (
            (mean - risk_free) / deviation * periods_per_year.sqrt()
            if len(returns) > 1 and deviation > 0
            else None
        )
        threshold = (
            spec.initial_equity * spec.ruin_equity_fraction
            if spec.ruin_equity_fraction is not None
            else Decimal(0)
        )
        ruined = not solvent or (threshold > 0 and min(equity) <= threshold)
        unavailable = []
        if cagr is None:
            unavailable.append(("cagr", "insolvent or terminal wealth is nonpositive"))
        if sharpe is None:
            unavailable.append(("sharpe", "requires at least two returns and positive deviation"))
        cvar_95, cvar_reason = _expected_shortfall(returns, Decimal("0.95"))
        if cvar_reason is not None:
            unavailable.append(("cvar_95", cvar_reason))
        return MonteCarloPath(
            index,
            sample_indices,
            returns,
            tuple(equity),
            terminal,
            cagr,
            sharpe,
            maximum_drawdown,
            longest_duration,
            cvar_95,
            ruined,
            tuple(unavailable),
        )


def run_simulation(spec: MonteCarloSpec) -> MonteCarloResult:
    """Run a deterministic bootstrap and summarize each finite path metric."""
    if not isinstance(spec, MonteCarloSpec):
        raise DomainError("MonteCarloSpec is required")
    paths = tuple(_path(spec, i, _indices(spec, i)) for i in range(spec.path_count))
    distributions: list[MetricDistribution] = []
    for name in ("terminal_wealth", "cagr", "maximum_drawdown", "drawdown_duration", "cvar_95"):
        values = tuple(
            Decimal(getattr(path, name)) for path in paths if getattr(path, name) is not None
        )
        distributions.append(metric_distribution(name, values, len(paths) - len(values)))
    sharpes = tuple(path.sharpe for path in paths if path.sharpe is not None)
    distributions.append(metric_distribution("sharpe", sharpes, len(paths) - len(sharpes)))
    with localcontext(_context()):
        ruin_probability = Decimal(sum(path.ruined for path in paths)) / Decimal(len(paths))
    return MonteCarloResult(
        spec.simulation_id,
        spec.fingerprint,
        spec.sample_fingerprint,
        spec.method.value,
        spec.seed,
        paths,
        tuple(distributions),
        ruin_probability if spec.ruin_equity_fraction is not None else None,
        "assumed_trade_frequency"
        if spec.method is BootstrapMethod.TRADE
        else "observations_per_year",
    )
