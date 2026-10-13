"""Percentile bootstrap intervals for estimators, not future wealth forecasts.

Coverage is asymptotic and conditional on the supplied sample and resampling
assumptions. This module does not establish stationarity or independence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum

from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments.models import canonical_hash, canonical_json
from quant_terminal.monte_carlo import MonteCarloSpec, run_simulation
from quant_terminal.monte_carlo.models import SampleKind


class BootstrapEstimator(StrEnum):
    MEAN_PERIODIC_RETURN = "mean_periodic_return"
    ANNUALIZED_SHARPE = "annualized_sharpe_sample_sd"


@dataclass(frozen=True, slots=True)
class BootstrapIntervalSpec:
    """Complete M11 sample plus estimator and dated sampling assumptions."""

    sampling: MonteCarloSpec
    estimator: BootstrapEstimator
    confidence: Decimal
    sample_start: datetime
    sample_end: datetime
    frequency: str
    resampling_assumption: str

    def __post_init__(self) -> None:
        if not isinstance(self.sampling, MonteCarloSpec):
            raise DomainError("Bootstrap intervals require a MonteCarloSpec")
        if self.sampling.sample_kind is not SampleKind.PERIODIC_RETURN:
            raise DomainError("Estimator intervals currently require periodic returns")
        if self.sampling.path_length != len(self.sampling.returns):
            raise DomainError("Estimator replicate length must equal original sample length")
        if self.sampling.path_count < 2 or len(self.sampling.returns) < 2:
            raise DomainError("At least two observations and two replicates are required")
        if not isinstance(self.estimator, BootstrapEstimator):
            raise DomainError("Unsupported bootstrap estimator")
        if (
            not isinstance(self.confidence, Decimal)
            or not self.confidence.is_finite()
            or not 0 < self.confidence < 1
        ):
            raise DomainError("confidence must be a finite Decimal strictly between zero and one")
        for stamp in (self.sample_start, self.sample_end):
            if not isinstance(stamp, datetime) or stamp.utcoffset() != timedelta(0):
                raise DomainError("Sample interval timestamps must be UTC aware")
        if self.sample_end <= self.sample_start:
            raise DomainError("Sample interval must have positive duration")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.frequency, self.resampling_assumption)
        ):
            raise DomainError("Named frequency and explicit resampling assumption are required")

    def to_dict(self) -> dict[str, object]:
        return {
            "sampling": self.sampling.to_dict(),
            "estimator": self.estimator.value,
            "confidence": self.confidence,
            "sample_start": self.sample_start.isoformat(),
            "sample_end": self.sample_end.isoformat(),
            "frequency": self.frequency,
            "resampling_assumption": self.resampling_assumption,
            "method": "percentile-estimator-bootstrap-v1",
            "percentile_convention": "linear_interpolation_at_p_times_n_minus_one",
            "invalid_replicate_policy": "withhold_interval_if_any_invalid",
        }

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.to_dict())

    def artifact_bytes(self, result: BootstrapIntervalResult) -> bytes:
        if result.specification_fingerprint != self.fingerprint:
            raise DomainError("Bootstrap result does not match specification")
        return canonical_json(
            {
                "specification": self.to_dict(),
                "result": result.to_dict(),
                "result_fingerprint": result.fingerprint,
            }
        )


@dataclass(frozen=True, slots=True)
class BootstrapIntervalResult:
    specification_fingerprint: str
    resampler_result_fingerprint: str
    point_estimate: Decimal | None
    lower: Decimal | None
    upper: Decimal | None
    replicate_estimates: tuple[Decimal | None, ...]
    sample_indices: tuple[tuple[int, ...], ...]
    valid_replicates: int
    invalid_replicates: int
    unavailable_reason: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "specification_fingerprint": self.specification_fingerprint,
            "resampler_result_fingerprint": self.resampler_result_fingerprint,
            "point_estimate": self.point_estimate,
            "lower": self.lower,
            "upper": self.upper,
            "replicate_estimates": self.replicate_estimates,
            "sample_indices": self.sample_indices,
            "valid_replicates": self.valid_replicates,
            "invalid_replicates": self.invalid_replicates,
            "unavailable_reason": self.unavailable_reason,
        }

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.to_dict())


def _estimate(values: tuple[Decimal, ...], spec: BootstrapIntervalSpec) -> Decimal | None:
    mean = sum(values, Decimal(0)) / Decimal(len(values))
    if spec.estimator is BootstrapEstimator.MEAN_PERIODIC_RETURN:
        return mean
    variance = sum(((value - mean) ** 2 for value in values), Decimal(0)) / (len(values) - 1)
    if variance == 0:
        return None
    assert spec.sampling.periods_per_year is not None
    frequency = Decimal(spec.sampling.periods_per_year)
    risk_free = ((1 + spec.sampling.annual_risk_free_rate).ln() / frequency).exp() - 1
    return (mean - risk_free) / variance.sqrt() * frequency.sqrt()


def _percentile(ordered: tuple[Decimal, ...], probability: Decimal) -> Decimal:
    position = probability * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def bootstrap_interval(spec: BootstrapIntervalSpec) -> BootstrapIntervalResult:
    """Resample at the original horizon and compute two-sided percentile limits.

    Undefined Sharpe replicates are retained. Any invalid replicate withholds the
    interval rather than silently conditioning the bootstrap distribution on success.
    """
    if not isinstance(spec, BootstrapIntervalSpec):
        raise DomainError("BootstrapIntervalSpec is required")
    simulation = run_simulation(spec.sampling)
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        point = _estimate(spec.sampling.returns, spec)
        estimates = tuple(
            _estimate(tuple(spec.sampling.returns[i] for i in path.sample_indices), spec)
            for path in simulation.paths
        )
        valid = tuple(sorted(value for value in estimates if value is not None))
        invalid = len(estimates) - len(valid)
        reason = None
        lower = upper = None
        if point is None:
            reason = "Original-sample estimator is undefined: zero sample standard deviation"
        elif invalid:
            reason = "Undefined replicate estimators: interval withheld without dropping replicates"
        else:
            tail = (1 - spec.confidence) / 2
            lower = _percentile(valid, tail)
            upper = _percentile(valid, 1 - tail)
        return BootstrapIntervalResult(
            spec.fingerprint,
            simulation.content_fingerprint,
            point,
            lower,
            upper,
            estimates,
            tuple(path.sample_indices for path in simulation.paths),
            len(valid),
            invalid,
            reason,
        )
