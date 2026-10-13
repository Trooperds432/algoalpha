"""Estimator bootstrap identity, analytical boundaries and unavailable states."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, localcontext

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.monte_carlo import BootstrapMethod, MonteCarloSpec, SampleKind
from quant_terminal.robustness.bootstrap import (
    BootstrapEstimator,
    BootstrapIntervalSpec,
    bootstrap_interval,
)

D = Decimal


def specification(returns: tuple[Decimal, ...] = (D("-0.1"), D("0.1"))) -> BootstrapIntervalSpec:
    return BootstrapIntervalSpec(
        MonteCarloSpec(
            simulation_id="ci",
            source_run_ids=("baseline",),
            source_fingerprint="a" * 64,
            returns=returns,
            method=BootstrapMethod.IID_RETURN,
            sample_kind=SampleKind.PERIODIC_RETURN,
            path_count=100,
            path_length=len(returns),
            seed=17,
            periods_per_year=252,
            currency="USD",
            initial_equity=D(100),
        ),
        BootstrapEstimator.MEAN_PERIODIC_RETURN,
        D("0.95"),
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2021, 1, 1, tzinfo=UTC),
        "daily_trading_sessions",
        "IID non-overlapping stationary periodic returns assumed by caller",
    )


def test_mean_interval_uses_original_estimator_horizon_and_is_reproducible() -> None:
    spec = specification()
    result = bootstrap_interval(spec)
    assert result.point_estimate == 0
    # Exact two-point empirical distribution: means are -.1, 0, +.1.
    assert result.lower == D("-0.1")
    assert result.upper == D("0.1")
    assert set(result.replicate_estimates) == {D("-0.1"), D(0), D("0.1")}
    assert all(len(indices) == 2 for indices in result.sample_indices)
    assert result.valid_replicates == 100
    assert result.invalid_replicates == 0
    with localcontext() as context:
        context.prec = 8
        repeated = bootstrap_interval(spec)
    assert result == repeated
    artifact = json.loads(spec.artifact_bytes(result))
    assert artifact["specification"]["sampling"]["returns"] == ["-0.1", "0.1"]
    assert artifact["specification"]["sampling"]["seed"] == 17
    assert artifact["specification"]["estimator"] == "mean_periodic_return"
    assert artifact["result_fingerprint"] == result.fingerprint
    assert bootstrap_interval(replace(spec, sampling=replace(spec.sampling, seed=18))) != result
    with pytest.raises(DomainError, match="does not match"):
        replace(spec, confidence=D("0.90")).artifact_bytes(result)


def test_constant_mean_is_degenerate_and_sharpe_is_unavailable() -> None:
    spec = specification((D("0.01"),) * 3)
    result = bootstrap_interval(spec)
    assert result.point_estimate == result.lower == result.upper == D("0.01")
    sharpe = bootstrap_interval(replace(spec, estimator=BootstrapEstimator.ANNUALIZED_SHARPE))
    assert sharpe.point_estimate is None
    assert sharpe.lower is sharpe.upper is None
    assert sharpe.invalid_replicates == 100
    assert sharpe.valid_replicates == 0
    assert sharpe.unavailable_reason is not None


def test_undefined_sharpe_replicates_are_not_silently_discarded() -> None:
    spec = replace(specification(), estimator=BootstrapEstimator.ANNUALIZED_SHARPE)
    result = bootstrap_interval(spec)
    assert result.point_estimate == 0
    assert 0 < result.invalid_replicates < 100
    assert result.valid_replicates + result.invalid_replicates == 100
    assert result.lower is result.upper is None
    assert "without dropping" in (result.unavailable_reason or "")


def test_sharpe_uses_explicit_risk_free_and_annualization() -> None:
    spec = specification((D("0.1"), D("0.3")))
    spec = replace(
        spec,
        estimator=BootstrapEstimator.ANNUALIZED_SHARPE,
        sampling=replace(
            spec.sampling,
            method=BootstrapMethod.BLOCK_RETURN,
            block_length=2,
            periods_per_year=1,
            annual_risk_free_rate=D("0.1"),
        ),
    )
    result = bootstrap_interval(spec)
    # Excess mean .1 / sample SD sqrt(.02) = 1/sqrt(2).
    assert float(result.point_estimate or 0) == pytest.approx(2**-0.5)
    assert result.lower == result.upper == result.point_estimate
    assert all(indices == (0, 1) for indices in result.sample_indices)


def test_blocks_preserve_segments_and_truncate_to_sample_length() -> None:
    spec = specification((D(0), D("0.1"), D("0.2"), D("0.3"), D("0.4")))
    spec = replace(
        spec,
        sampling=replace(
            spec.sampling,
            method=BootstrapMethod.BLOCK_RETURN,
            block_length=2,
            segment_ids=("a", "a", "b", "b", "b"),
        ),
        resampling_assumption="Stationary blocks within declared source segments",
    )
    result = bootstrap_interval(spec)
    for indices in result.sample_indices:
        assert len(indices) == 5
        for start in (0, 2):
            assert indices[start + 1] == indices[start] + 1
            assert (
                spec.sampling.segment_ids[indices[start]]
                == spec.sampling.segment_ids[indices[start + 1]]
            )


def test_rejects_forecast_horizon_and_invalid_metadata() -> None:
    spec = specification()
    with pytest.raises(DomainError, match="original sample length"):
        replace(spec, sampling=replace(spec.sampling, path_length=20))
    with pytest.raises(DomainError, match="confidence"):
        replace(spec, confidence=D(1))
    with pytest.raises(DomainError, match="UTC"):
        replace(spec, sample_start=datetime(2020, 1, 1))
    with pytest.raises(DomainError, match="frequency"):
        replace(spec, frequency="")
    with pytest.raises(DomainError, match="two replicates"):
        replace(spec, sampling=replace(spec.sampling, path_count=1))
    with pytest.raises(DomainError, match="periodic returns"):
        replace(
            spec,
            sampling=replace(
                spec.sampling,
                method=BootstrapMethod.TRADE,
                sample_kind=SampleKind.CLOSED_TRADE_RETURN,
                periods_per_year=None,
                trade_frequency_per_year=D(30),
            ),
        )
