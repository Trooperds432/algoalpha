"""PSR formula, identity and fail-closed assumption tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.robustness import PeriodicReturnSample, probabilistic_sharpe
from quant_terminal.robustness.sharpe import _normal_cdf


def _sample(
    values: tuple[Decimal, ...] | None = None,
    *,
    benchmark: Decimal = Decimal(0),
) -> PeriodicReturnSample:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    returns = values or tuple(
        Decimal("0.01") if index % 2 else Decimal("-0.01") for index in range(30)
    )
    return PeriodicReturnSample(
        "sample-1",
        ("m8-run-1",),
        "a" * 64,
        "b" * 64,
        "USD",
        "daily-xnys-session",
        252,
        "annual-effective:0",
        "benchmark-none",
        benchmark,
        tuple((start + timedelta(days=index), index, value) for index, value in enumerate(returns)),
    )


def test_psr_formula_and_fingerprint_are_deterministic() -> None:
    sample = _sample()
    result = probabilistic_sharpe(sample, iid_nonoverlapping_attested=True)
    assert result.observations == 30
    assert result.sharpe_per_period == 0
    assert result.skew == 0
    assert result.excess_kurtosis == Decimal(-2)
    assert result.denominator == 1
    assert result.probability == Decimal("0.5")
    assert result.source_run_ids == ("m8-run-1",)
    assert result.currency == "USD"
    assert result.sample_fingerprint == sample.fingerprint
    assert (
        result.fingerprint
        == probabilistic_sharpe(sample, iid_nonoverlapping_attested=True).fingerprint
    )


def test_psr_converts_annual_threshold_to_periodic_units() -> None:
    result = probabilistic_sharpe(
        _sample(benchmark=Decimal("1.5")), iid_nonoverlapping_attested=True
    )
    assert (
        result.benchmark_sharpe_per_period * Decimal(252).sqrt() - Decimal("1.5")
    ).copy_abs() < Decimal("1e-32")
    assert result.probability < Decimal("0.5")


def test_normal_cdf_matches_reference_values() -> None:
    assert abs(_normal_cdf(1.0) - 0.8413447460685429) < 1e-15
    assert abs(_normal_cdf(-1.0) - 0.15865525393145707) < 1e-15


def test_psr_non_normal_reference_fixture() -> None:
    returns = tuple(
        Decimal("-0.04") if index == 0 else Decimal("0.01") if index < 20 else Decimal("0.02")
        for index in range(30)
    )
    result = probabilistic_sharpe(
        _sample(returns, benchmark=Decimal("0.5")), iid_nonoverlapping_attested=True
    )
    assert result.skew == Decimal("-3.626278568730560350705998349934526")
    assert result.excess_kurtosis == Decimal("15.43723973825104104699583581201668")
    assert result.probability == Decimal("0.962678482551408")


def test_psr_rejects_unattested_or_insufficient_observations() -> None:
    with pytest.raises(DomainError, match="explicit IID"):
        probabilistic_sharpe(_sample(), iid_nonoverlapping_attested=False)
    with pytest.raises(DomainError, match="30"):
        probabilistic_sharpe(
            _sample(tuple(Decimal(index) for index in range(29))),
            iid_nonoverlapping_attested=True,
        )


def test_psr_rejects_duplicate_or_gapped_period_indices_and_zero_variance() -> None:
    sample = _sample()
    rows = sample.observations
    prefix = (
        sample.sample_id,
        sample.source_run_ids,
        sample.source_fingerprint,
        sample.source_attestation_fingerprint,
        sample.currency,
        sample.frequency,
        sample.periods_per_year,
        sample.risk_free_basis,
        sample.benchmark_identity,
        sample.annual_benchmark_sharpe,
    )
    with pytest.raises(DomainError, match="increase strictly"):
        PeriodicReturnSample(
            *prefix,
            ((rows[0][0], 0, rows[0][2]), (rows[0][0], 1, rows[1][2]), *rows[2:]),
        )
    with pytest.raises(DomainError, match="noncontiguous session"):
        PeriodicReturnSample(
            *prefix,
            ((rows[0][0], 0, rows[0][2]), (rows[1][0], 2, rows[1][2]), *rows[2:]),
        )
    constant = _sample(tuple(Decimal("0.01") for _ in range(30)))
    with pytest.raises(DomainError, match="positive sample deviation"):
        probabilistic_sharpe(constant, iid_nonoverlapping_attested=True)
