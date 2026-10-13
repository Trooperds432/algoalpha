"""Conditional Probabilistic Sharpe diagnostic with explicit IID assumptions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from math import isfinite
from statistics import NormalDist

from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments.models import canonical_hash


def _context() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN)


def _normal_cdf(value: float) -> float:
    """Versioned standard-library normal CDF used by PSR v1."""
    if not isfinite(value):
        raise DomainError("Normal-CDF input must be finite")
    return NormalDist().cdf(value)


@dataclass(frozen=True, slots=True)
class PeriodicReturnSample:
    """Adapter-attested aligned M8 excess returns on contiguous sample periods."""

    sample_id: str
    source_run_ids: tuple[str, ...]
    source_fingerprint: str
    source_attestation_fingerprint: str
    currency: str
    frequency: str
    periods_per_year: int
    risk_free_basis: str
    benchmark_identity: str
    annual_benchmark_sharpe: Decimal
    observations: tuple[tuple[datetime, int, Decimal], ...]

    def __post_init__(self) -> None:
        for name in ("sample_id", "frequency", "risk_free_basis", "benchmark_identity"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DomainError(f"{name} must be nonempty")
        run_ids = tuple(self.source_run_ids)
        if not run_ids or any(not isinstance(item, str) or not item.strip() for item in run_ids):
            raise DomainError("Periodic samples require source M8 run identities")
        if len(set(run_ids)) != len(run_ids):
            raise DomainError("Periodic sample source run IDs must be unique")
        object.__setattr__(self, "source_run_ids", run_ids)
        for name in ("source_fingerprint", "source_attestation_fingerprint"):
            value = getattr(self, name)
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
                raise DomainError(f"{name} must be a SHA-256 digest")
            object.__setattr__(self, name, value.lower())
        if not isinstance(self.currency, str) or re.fullmatch(r"[A-Z]{3}", self.currency) is None:
            raise DomainError("currency must be an uppercase three-letter code")
        if type(self.periods_per_year) is not int or self.periods_per_year <= 0:
            raise DomainError("periods_per_year must be a positive integer")
        if (
            not isinstance(self.annual_benchmark_sharpe, Decimal)
            or not self.annual_benchmark_sharpe.is_finite()
        ):
            raise DomainError("annual_benchmark_sharpe must be a finite Decimal")
        rows = tuple(self.observations)
        if not rows:
            raise DomainError("Periodic return observations must be nonempty")
        normalized: list[tuple[datetime, int, Decimal]] = []
        previous_time: datetime | None = None
        previous_period: int | None = None
        for row in rows:
            if not isinstance(row, tuple) or len(row) != 3:
                raise DomainError("Each observation needs timestamp, period index, and return")
            timestamp, period_index, excess_return = row
            if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
                raise DomainError("Periodic sample timestamps must be timezone-aware")
            when = utc(timestamp)
            if previous_time is not None and when <= previous_time:
                raise DomainError("Periodic sample timestamps must increase strictly")
            if type(period_index) is not int:
                raise DomainError("Periodic session indices must be integers")
            if previous_period is not None and period_index != previous_period + 1:
                raise DomainError("Periodic sample contains a missing or noncontiguous session")
            if not isinstance(excess_return, Decimal) or not excess_return.is_finite():
                raise DomainError("Periodic excess returns must be finite Decimals")
            normalized.append((when, period_index, excess_return))
            previous_time = when
            previous_period = period_index
        object.__setattr__(self, "observations", tuple(normalized))

    @property
    def fingerprint(self) -> str:
        return canonical_hash(
            {
                "sample_id": self.sample_id,
                "source_run_ids": self.source_run_ids,
                "source_fingerprint": self.source_fingerprint,
                "source_attestation_fingerprint": self.source_attestation_fingerprint,
                "currency": self.currency,
                "frequency": self.frequency,
                "periods_per_year": self.periods_per_year,
                "risk_free_basis": self.risk_free_basis,
                "benchmark_identity": self.benchmark_identity,
                "annual_benchmark_sharpe": self.annual_benchmark_sharpe,
                "observations": self.observations,
            }
        )


@dataclass(frozen=True, slots=True)
class ProbabilisticSharpeResult:
    """A PSR estimate and all moments and source identity needed to interpret it."""

    sample_fingerprint: str
    source_run_ids: tuple[str, ...]
    source_attestation_fingerprint: str
    currency: str
    frequency: str
    risk_free_basis: str
    benchmark_identity: str
    observations: int
    periods_per_year: int
    sharpe_per_period: Decimal
    benchmark_sharpe_per_period: Decimal
    skew: Decimal
    excess_kurtosis: Decimal
    denominator: Decimal
    probability: Decimal
    method: str = "bailey-lopez-de-prado-psr-v1"
    cdf_method: str = "statistics-normaldist-cdf-v1"
    assumption: str = "user-attested-iid-nonoverlapping-periodic-excess-returns"

    def to_dict(self) -> dict[str, object]:
        return {
            "method": self.method,
            "cdf_method": self.cdf_method,
            "assumption": self.assumption,
            "sample_fingerprint": self.sample_fingerprint,
            "source_run_ids": self.source_run_ids,
            "source_attestation_fingerprint": self.source_attestation_fingerprint,
            "currency": self.currency,
            "frequency": self.frequency,
            "risk_free_basis": self.risk_free_basis,
            "benchmark_identity": self.benchmark_identity,
            "observations": self.observations,
            "periods_per_year": self.periods_per_year,
            "sharpe_per_period": str(self.sharpe_per_period),
            "benchmark_sharpe_per_period": str(self.benchmark_sharpe_per_period),
            "skew": str(self.skew),
            "excess_kurtosis": str(self.excess_kurtosis),
            "denominator": str(self.denominator),
            "probability": str(self.probability),
        }

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.to_dict())


def probabilistic_sharpe(
    sample: PeriodicReturnSample, *, iid_nonoverlapping_attested: bool
) -> ProbabilisticSharpeResult:
    """Calculate PSR from a provenance-bound periodic return sample under IID claim."""
    if not isinstance(sample, PeriodicReturnSample):
        raise DomainError("An adapter-attested PeriodicReturnSample is required")
    if type(iid_nonoverlapping_attested) is not bool or not iid_nonoverlapping_attested:
        raise DomainError("PSR requires explicit IID, non-overlapping return attestation")
    if len(sample.observations) < 30:
        raise DomainError("PSR requires at least 30 periodic observations")

    with localcontext(_context()):
        values = tuple(value for _, _, value in sample.observations)
        count = Decimal(len(values))
        mean = sum(values, Decimal(0)) / count
        centered = tuple(value - mean for value in values)
        variance = sum((value * value for value in centered), Decimal(0)) / (count - 1)
        deviation = variance.sqrt()
        if deviation <= 0:
            raise DomainError("PSR requires positive sample deviation")
        sharpe = mean / deviation
        population_m2 = sum((value * value for value in centered), Decimal(0)) / count
        if population_m2 <= 0:
            raise DomainError("PSR requires positive centered second moment")
        scale = population_m2.sqrt()
        skew = sum((value**3 for value in centered), Decimal(0)) / count / (scale**3)
        raw_kurtosis = (
            sum((value**4 for value in centered), Decimal(0)) / count / (population_m2**2)
        )
        excess_kurtosis = raw_kurtosis - Decimal(3)
        target = sample.annual_benchmark_sharpe / Decimal(sample.periods_per_year).sqrt()
        variance_term = (
            Decimal(1) - skew * sharpe + (excess_kurtosis + Decimal(2)) / Decimal(4) * sharpe**2
        )
        if not variance_term.is_finite() or variance_term <= 0:
            raise DomainError("PSR moment adjustment must be finite and positive")
        denominator = variance_term.sqrt()
        z_score = (sharpe - target) * (count - 1).sqrt() / denominator
        z_float = float(z_score)
        if not isfinite(z_float):
            raise DomainError("PSR normal-CDF input is outside the supported numeric range")
        probability = Decimal(str(_normal_cdf(z_float)))

    return ProbabilisticSharpeResult(
        sample.fingerprint,
        sample.source_run_ids,
        sample.source_attestation_fingerprint,
        sample.currency,
        sample.frequency,
        sample.risk_free_basis,
        sample.benchmark_identity,
        len(sample.observations),
        sample.periods_per_year,
        sharpe,
        target,
        skew,
        excess_kurtosis,
        denominator,
        probability,
    )
