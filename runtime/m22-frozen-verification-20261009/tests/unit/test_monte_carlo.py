"""Reproducible bootstrap method, metric and input-boundary tests."""

import json
from decimal import Decimal, getcontext

import pytest

from quant_terminal.core.exceptions import DomainError
from quant_terminal.monte_carlo import (
    BootstrapMethod,
    MonteCarloSpec,
    SampleKind,
    run_simulation,
)

RETURNS = (
    Decimal("0.10"),
    Decimal("-0.05"),
    Decimal("0.02"),
    Decimal("-0.20"),
    Decimal("0.04"),
    Decimal("0.01"),
)


def _spec(
    method: BootstrapMethod = BootstrapMethod.IID_RETURN,
    *,
    seed: int = 42,
    block_length: int | None = None,
    segment_ids: tuple[str, ...] = (),
    returns: tuple[Decimal, ...] = RETURNS,
    path_length: int = 12,
    ruin: Decimal | None = Decimal("0.5"),
) -> MonteCarloSpec:
    return MonteCarloSpec(
        "mc-test",
        ("m10-run-1",),
        "a" * 64,
        returns,
        method,
        SampleKind.CLOSED_TRADE_RETURN
        if method is BootstrapMethod.TRADE
        else SampleKind.PERIODIC_RETURN,
        100,
        path_length,
        seed,
        None if method is BootstrapMethod.TRADE else 252,
        "USD",
        Decimal("10000"),
        segment_ids,
        block_length,
        ruin,
        trade_frequency_per_year=Decimal("100") if method is BootstrapMethod.TRADE else None,
    )


def test_iid_simulation_is_reproducible_and_independent_of_decimal_context() -> None:
    spec = _spec()
    before = run_simulation(spec)
    getcontext().prec = 6
    after = run_simulation(spec)
    getcontext().prec = 28
    assert before == after
    assert before.content_fingerprint == after.content_fingerprint
    assert before.to_dict() == after.to_dict()
    assert before.to_json_bytes() == after.to_json_bytes()
    artifact = json.loads(spec.artifact_bytes(before))
    assert artifact["specification"]["source_run_ids"] == ["m10-run-1"]
    assert artifact["specification"]["algorithm"] == "sha256-index-rejection-v1"
    assert artifact["specification"]["currency"] == "USD"
    assert artifact["result_fingerprint"] == before.content_fingerprint
    assert spec.to_dict()["fingerprint"] == spec.fingerprint
    assert len(before.paths) == 100
    assert before.paths[0].sample_indices == (5, 3, 3, 5, 2, 4, 4, 2, 3, 0, 2, 5)
    assert all(len(path.returns) == 12 and len(path.equity) == 13 for path in before.paths)
    assert {item.metric for item in before.distributions} >= {
        "terminal_wealth",
        "cagr",
        "sharpe",
        "maximum_drawdown",
        "drawdown_duration",
        "cvar_95",
    }
    cvar = next(item for item in before.distributions if item.metric == "cvar_95")
    assert cvar.observations == 0 and cvar.unavailable_observations == 100
    assert cvar.p95 is None


def test_cvar_is_reported_only_when_the_m6_tail_sample_is_sufficient() -> None:
    spec = _spec(path_length=120)
    result = run_simulation(spec)
    cvar = next(item for item in result.distributions if item.metric == "cvar_95")
    assert cvar.observations == 100
    assert all(path.cvar_95 is not None for path in result.paths)


def test_trade_and_iid_methods_use_a_seeded_sample_with_replacement() -> None:
    trade = run_simulation(_spec(BootstrapMethod.TRADE))
    changed_seed = run_simulation(_spec(BootstrapMethod.TRADE, seed=43))
    same_sample_different_seed = _spec(BootstrapMethod.TRADE, seed=43)
    assert _spec(BootstrapMethod.TRADE).sample_fingerprint == (
        same_sample_different_seed.sample_fingerprint
    )
    assert all(len(path.sample_indices) == 12 for path in trade.paths)
    assert any(len(set(path.sample_indices)) < len(path.sample_indices) for path in trade.paths)
    assert tuple(path.sample_indices for path in trade.paths) != tuple(
        path.sample_indices for path in changed_seed.paths
    )


def test_block_bootstrap_stays_inside_contiguous_segment_boundaries() -> None:
    segments = ("a", "a", "a", "b", "b", "b")
    result = run_simulation(
        _spec(BootstrapMethod.BLOCK_RETURN, block_length=2, segment_ids=segments)
    )
    for path in result.paths:
        assert len(path.sample_indices) == 12
        for offset in range(0, len(path.sample_indices), 2):
            block = path.sample_indices[offset : offset + 2]
            assert len({segments[index] for index in block}) == 1


def test_block_bootstrap_requires_segments_for_multiple_sources() -> None:
    with pytest.raises(DomainError, match="explicit segment_ids"):
        MonteCarloSpec(
            "multi-source-block",
            ("m8-run-a", "m8-run-b"),
            "a" * 64,
            RETURNS,
            BootstrapMethod.BLOCK_RETURN,
            SampleKind.PERIODIC_RETURN,
            10,
            12,
            42,
            252,
            "USD",
            Decimal("10000"),
            block_length=2,
        )

    segments = ("run-a", "run-a", "run-a", "run-b", "run-b", "run-b")
    result = run_simulation(
        _spec(BootstrapMethod.BLOCK_RETURN, block_length=2, segment_ids=segments)
    )
    for path in result.paths:
        for offset in range(0, len(path.sample_indices), 2):
            block = path.sample_indices[offset : offset + 2]
            assert len({segments[index] for index in block}) == 1


def test_path_arithmetic_insolvency_and_ruin_are_explicit() -> None:
    spec = _spec(returns=(Decimal("-1"),))
    path = run_simulation(spec).paths[0]
    assert Decimal(0) in path.equity
    assert path.terminal_wealth == 0
    assert path.cagr is None
    assert path.ruined
    assert ("cagr", "insolvent or terminal wealth is nonpositive") in path.unavailable_reasons

    leveraged = run_simulation(_spec(returns=(Decimal("-1.5"),), path_length=1)).paths[0]
    assert leveraged.terminal_wealth == Decimal("-5000")
    assert leveraged.maximum_drawdown == Decimal("1.5")
    assert leveraged.cagr is None and leveraged.ruined


def test_invalid_sample_or_block_configuration_fails_closed() -> None:
    with pytest.raises(DomainError, match="nonempty finite"):
        _spec(returns=())
    with pytest.raises(DomainError, match="No complete block"):
        _spec(
            BootstrapMethod.BLOCK_RETURN,
            block_length=2,
            segment_ids=("a", "b", "c", "d", "e", "f"),
        )
    with pytest.raises(DomainError, match="block_length"):
        _spec(BootstrapMethod.IID_RETURN, block_length=2)
    with pytest.raises(DomainError, match="incompatible"):
        MonteCarloSpec(
            "wrong-kind",
            ("run",),
            "a" * 64,
            RETURNS,
            BootstrapMethod.TRADE,
            SampleKind.PERIODIC_RETURN,
            1,
            1,
            1,
            None,
            "USD",
            Decimal("10000"),
            trade_frequency_per_year=Decimal("100"),
        )
    with pytest.raises(DomainError, match="budget"):
        MonteCarloSpec(
            "too-large",
            ("run",),
            "a" * 64,
            RETURNS,
            BootstrapMethod.IID_RETURN,
            SampleKind.PERIODIC_RETURN,
            100_000,
            101,
            1,
            252,
            "USD",
            Decimal("10000"),
        )
