"""Immutable Monte Carlo sample, specification, path and summary records."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import cast

from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments.models import canonical_hash, canonical_json


class BootstrapMethod(StrEnum):
    TRADE = "trade_bootstrap"
    IID_RETURN = "iid_return_bootstrap"
    BLOCK_RETURN = "moving_block_bootstrap"


class SampleKind(StrEnum):
    CLOSED_TRADE_RETURN = "closed_trade_return"
    PERIODIC_RETURN = "periodic_return"


@dataclass(frozen=True, slots=True)
class MonteCarloSpec:
    simulation_id: str
    source_run_ids: tuple[str, ...]
    source_fingerprint: str
    returns: tuple[Decimal, ...]
    method: BootstrapMethod
    sample_kind: SampleKind
    path_count: int
    path_length: int
    seed: int
    periods_per_year: int | None
    currency: str
    initial_equity: Decimal
    segment_ids: tuple[str, ...] = ()
    block_length: int | None = None
    ruin_equity_fraction: Decimal | None = None
    annual_risk_free_rate: Decimal = Decimal(0)
    trade_frequency_per_year: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.simulation_id, str) or not self.simulation_id.strip():
            raise DomainError("simulation_id must be nonempty")
        run_ids = tuple(self.source_run_ids)
        if not run_ids or any(
            not isinstance(run_id, str) or not run_id.strip() for run_id in run_ids
        ):
            raise DomainError("Monte Carlo input must name source run identities")
        if len(set(run_ids)) != len(run_ids):
            raise DomainError("Source run identities must be unique")
        object.__setattr__(self, "source_run_ids", run_ids)
        if re.fullmatch(r"[0-9a-fA-F]{64}", self.source_fingerprint) is None:
            raise DomainError("source_fingerprint must be a SHA-256 digest")
        object.__setattr__(self, "source_fingerprint", self.source_fingerprint.lower())
        returns = tuple(self.returns)
        if not returns or any(
            not isinstance(value, Decimal) or not value.is_finite() for value in returns
        ):
            raise DomainError("Sample returns must be nonempty finite Decimals")
        object.__setattr__(self, "returns", returns)
        if not isinstance(self.method, BootstrapMethod):
            raise DomainError("A supported bootstrap method is required")
        if not isinstance(self.sample_kind, SampleKind):
            raise DomainError("A supported Monte Carlo sample kind is required")
        if (
            self.method is BootstrapMethod.TRADE
            and self.sample_kind is not SampleKind.CLOSED_TRADE_RETURN
        ) or (
            self.method is not BootstrapMethod.TRADE
            and self.sample_kind is not SampleKind.PERIODIC_RETURN
        ):
            raise DomainError("Bootstrap method and return sample kind are incompatible")
        if type(self.path_count) is not int or not 1 <= self.path_count <= 100_000:
            raise DomainError("path_count must be in [1, 100000]")
        if type(self.path_length) is not int or not 1 <= self.path_length <= 1_000_000:
            raise DomainError("path_length must be in [1, 1000000]")
        if self.path_count * self.path_length > 10_000_000:
            raise DomainError("Simulation exceeds the bounded 10-million-observation budget")
        if type(self.seed) is not int or not 0 <= self.seed < 2**64:
            raise DomainError("seed must be an unsigned 64-bit integer")
        if (
            not isinstance(self.initial_equity, Decimal)
            or not self.initial_equity.is_finite()
            or (self.initial_equity <= 0)
        ):
            raise DomainError("initial_equity must be a positive finite Decimal")
        if not isinstance(self.currency, str) or re.fullmatch(r"[A-Z]{3}", self.currency) is None:
            raise DomainError("currency must be an uppercase three-letter code")
        if (
            self.method is BootstrapMethod.BLOCK_RETURN
            and len(run_ids) > 1
            and not self.segment_ids
        ):
            raise DomainError(
                "Block bootstrap requires explicit segment_ids when multiple source runs are used"
            )
        segments = tuple(self.segment_ids) if self.segment_ids else ("default",) * len(returns)
        if len(segments) != len(returns) or any(
            not isinstance(item, str) or not item.strip() for item in segments
        ):
            raise DomainError("segment_ids must provide one nonempty ID per return")
        object.__setattr__(self, "segment_ids", segments)
        if self.method is BootstrapMethod.TRADE and self.block_length is not None:
            raise DomainError("Trade bootstrap does not use block_length")
        if self.method is BootstrapMethod.BLOCK_RETURN:
            if type(self.block_length) is not int or not 1 <= self.block_length <= len(returns):
                raise DomainError("Block bootstrap requires a valid positive block_length")
            length = self.block_length
            if not any(
                len(set(segments[start : start + length])) == 1
                for start in range(len(returns) - length + 1)
            ):
                raise DomainError("No complete block remains inside one sample segment")
        elif self.block_length is not None:
            raise DomainError("block_length is only valid for moving block bootstrap")
        if self.ruin_equity_fraction is not None and (
            not isinstance(self.ruin_equity_fraction, Decimal)
            or not self.ruin_equity_fraction.is_finite()
            or not 0 < self.ruin_equity_fraction <= 1
        ):
            raise DomainError("ruin_equity_fraction must be in (0, 1]")
        if (
            not isinstance(self.annual_risk_free_rate, Decimal)
            or not self.annual_risk_free_rate.is_finite()
            or self.annual_risk_free_rate <= -1
        ):
            raise DomainError("annual_risk_free_rate must be finite and greater than -1")
        if self.method is BootstrapMethod.TRADE:
            if self.periods_per_year is not None:
                raise DomainError(
                    "Trade bootstrap uses trade_frequency_per_year, not periods_per_year"
                )
            if (
                not isinstance(self.trade_frequency_per_year, Decimal)
                or not self.trade_frequency_per_year.is_finite()
                or self.trade_frequency_per_year <= 0
            ):
                raise DomainError(
                    "Trade bootstrap requires an explicit positive trade_frequency_per_year"
                )
        elif self.trade_frequency_per_year is not None:
            raise DomainError("trade_frequency_per_year applies only to trade bootstrap")
        elif type(self.periods_per_year) is not int or self.periods_per_year <= 0:
            raise DomainError("periods_per_year must be positive for periodic-return methods")

    @property
    def sample_fingerprint(self) -> str:
        return canonical_hash(
            {
                "returns": self.returns,
                "segment_ids": self.segment_ids,
                "source_run_ids": self.source_run_ids,
                "source_fingerprint": self.source_fingerprint,
            }
        )

    def to_dict(self) -> dict[str, object]:
        """Return complete immutable inputs in canonical JSON-compatible form."""
        return cast(
            dict[str, object],
            json.loads(
                canonical_json(
                    {
                        "simulation_id": self.simulation_id,
                        "source_run_ids": self.source_run_ids,
                        "source_fingerprint": self.source_fingerprint,
                        "sample_fingerprint": self.sample_fingerprint,
                        "returns": self.returns,
                        "method": self.method.value,
                        "sample_kind": self.sample_kind.value,
                        "path_count": self.path_count,
                        "path_length": self.path_length,
                        "seed": self.seed,
                        "periods_per_year": self.periods_per_year,
                        "currency": self.currency,
                        "initial_equity": self.initial_equity,
                        "segment_ids": self.segment_ids,
                        "block_length": self.block_length,
                        "ruin_equity_fraction": self.ruin_equity_fraction,
                        "annual_risk_free_rate": self.annual_risk_free_rate,
                        "trade_frequency_per_year": self.trade_frequency_per_year,
                        "algorithm": "sha256-index-rejection-v1",
                        "fingerprint": self.fingerprint,
                    }
                )
            ),
        )

    @property
    def fingerprint(self) -> str:
        return canonical_hash(
            {
                "algorithm": "sha256-index-rejection-v1",
                "simulation_id": self.simulation_id,
                "sample_fingerprint": self.sample_fingerprint,
                "method": self.method.value,
                "sample_kind": self.sample_kind.value,
                "path_count": self.path_count,
                "path_length": self.path_length,
                "seed": self.seed,
                "periods_per_year": self.periods_per_year,
                "currency": self.currency,
                "initial_equity": self.initial_equity,
                "block_length": self.block_length,
                "ruin_equity_fraction": self.ruin_equity_fraction,
                "annual_risk_free_rate": self.annual_risk_free_rate,
                "trade_frequency_per_year": self.trade_frequency_per_year,
            }
        )

    def artifact_bytes(self, result: MonteCarloResult) -> bytes:
        """Bind the complete source/sample specification and result in one artifact."""
        if (
            not isinstance(result, MonteCarloResult)
            or result.simulation_id != self.simulation_id
            or result.specification_fingerprint != self.fingerprint
            or result.sample_fingerprint != self.sample_fingerprint
        ):
            raise DomainError("Monte Carlo result does not match the complete specification")
        return canonical_json(
            {
                "specification": self.to_dict(),
                "result": result.to_dict(),
                "result_fingerprint": result.content_fingerprint,
            }
        )


@dataclass(frozen=True, slots=True)
class MonteCarloPath:
    index: int
    sample_indices: tuple[int, ...]
    returns: tuple[Decimal, ...]
    equity: tuple[Decimal, ...]
    terminal_wealth: Decimal
    cagr: Decimal | None
    sharpe: Decimal | None
    maximum_drawdown: Decimal
    drawdown_duration: int
    cvar_95: Decimal | None
    ruined: bool
    unavailable_reasons: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class MetricDistribution:
    metric: str
    p05: Decimal | None
    p25: Decimal | None
    p50: Decimal | None
    p75: Decimal | None
    p95: Decimal | None
    observations: int
    unavailable_observations: int = 0


@dataclass(frozen=True, slots=True)
class MonteCarloResult:
    simulation_id: str
    specification_fingerprint: str
    sample_fingerprint: str
    method: str
    seed: int
    paths: tuple[MonteCarloPath, ...]
    distributions: tuple[MetricDistribution, ...]
    risk_of_ruin: Decimal | None
    metric_basis: str

    def to_dict(self) -> dict[str, object]:
        """Serialize paths, indices, metrics and distributions without executable data."""
        return cast(
            dict[str, object],
            json.loads(
                canonical_json(
                    {
                        "simulation_id": self.simulation_id,
                        "specification_fingerprint": self.specification_fingerprint,
                        "sample_fingerprint": self.sample_fingerprint,
                        "method": self.method,
                        "seed": self.seed,
                        "paths": [
                            {
                                "index": path.index,
                                "sample_indices": path.sample_indices,
                                "returns": path.returns,
                                "equity": path.equity,
                                "terminal_wealth": path.terminal_wealth,
                                "cagr": path.cagr,
                                "sharpe": path.sharpe,
                                "maximum_drawdown": path.maximum_drawdown,
                                "drawdown_duration": path.drawdown_duration,
                                "cvar_95": path.cvar_95,
                                "ruined": path.ruined,
                                "unavailable_reasons": path.unavailable_reasons,
                            }
                            for path in self.paths
                        ],
                        "distributions": [
                            {
                                "metric": item.metric,
                                "p05": item.p05,
                                "p25": item.p25,
                                "p50": item.p50,
                                "p75": item.p75,
                                "p95": item.p95,
                                "observations": item.observations,
                                "unavailable_observations": item.unavailable_observations,
                            }
                            for item in self.distributions
                        ],
                        "risk_of_ruin": self.risk_of_ruin,
                        "metric_basis": self.metric_basis,
                    }
                )
            ),
        )

    def to_json_bytes(self) -> bytes:
        """Produce canonical bytes suitable for retention as an M8 artifact."""
        return canonical_json(self.to_dict())

    @property
    def content_fingerprint(self) -> str:
        return hashlib.sha256(self.to_json_bytes()).hexdigest()


def metric_distribution(
    name: str, values: tuple[Decimal, ...], unavailable: int = 0
) -> MetricDistribution:
    """Calculate M6-compatible linear empirical percentiles over finite values."""
    if any(not value.is_finite() for value in values):
        raise DomainError("Percentile summaries require finite path metrics")
    if not values:
        if unavailable <= 0:
            raise DomainError("A missing percentile distribution needs unavailable observations")
        return MetricDistribution(name, None, None, None, None, None, 0, unavailable)
    ordered = sorted(values)
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):

        def quantile(p: Decimal) -> Decimal:
            position = p * Decimal(len(ordered) - 1)
            low = int(position)
            high = min(low + 1, len(ordered) - 1)
            return ordered[low] + (ordered[high] - ordered[low]) * (position - Decimal(low))

        return MetricDistribution(
            name,
            quantile(Decimal("0.05")),
            quantile(Decimal("0.25")),
            quantile(Decimal("0.50")),
            quantile(Decimal("0.75")),
            quantile(Decimal("0.95")),
            len(ordered),
            unavailable,
        )
