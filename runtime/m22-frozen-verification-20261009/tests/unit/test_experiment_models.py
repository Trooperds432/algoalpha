"""Golden tests for canonical, immutable experiment identities."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

import pytest

from quant_terminal.analytics.metrics import Metric
from quant_terminal.backtesting.models import BacktestResult, NAVPoint
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import RunId
from quant_terminal.experiments.models import (
    DatasetIdentity,
    ExperimentConfig,
    ExperimentManifest,
    canonical_hash,
    canonical_json,
    result_fingerprint,
)

START = datetime(2024, 1, 2, tzinfo=UTC)


def _dataset() -> DatasetIdentity:
    return DatasetIdentity("a" * 64, "b" * 64, "c" * 64, 20, ("ETF-1",))


def _config(
    *, seed: int = 7, parameters: dict[str, object] | None = None, commit: str | None = None
) -> ExperimentConfig:
    return ExperimentConfig(
        "simple-momentum-v1",
        "Simple Momentum",
        "d" * 64,
        "e" * 64,
        commit,
        "0.0.1",
        "f" * 64,
        (_dataset(),),
        START,
        START + timedelta(days=2),
        "USD",
        Decimal("1000.00"),
        parameters if parameters is not None else {"lookback": 3},
        seed,
        {"allow_short": False, "zero_cost": False},
        {"annualization": 252},
        None,
        {"commission": {"kind": "per_unit", "rate": Decimal("0.01")}},
    )


def _result(nav: str = "1010", name: str = "Simple Momentum") -> BacktestResult:
    values = (Decimal("1000"), Decimal(nav))
    points = tuple(
        NAVPoint(
            timestamp=START + timedelta(days=index),
            nav=value,
            period_return=None,
            cash=value,
            positions=(),
            realized_pnl=Decimal(0),
            unrealized_pnl=Decimal(0),
            long_exposure=Decimal(0),
            short_exposure=Decimal(0),
            gross_exposure=Decimal(0),
            net_exposure=Decimal(0),
            leverage=None,
            reconciliation_error=Decimal(0),
        )
        for index, value in enumerate(values)
    )
    return BacktestResult(
        name,
        START,
        START + timedelta(days=2),
        points,
        (),
        (),
        (),
        Decimal(0),
        Decimal(0),
        (),
        (),
    )


def _manifest(config: ExperimentConfig | None = None) -> ExperimentManifest:
    return ExperimentManifest(
        RunId("run-1"),
        START,
        config or _config(),
        result_fingerprint(_result()),
        (("sharpe", Metric(Decimal("1.25"), "ratio", 20)),),
        "Python 3.13.15",
        {"platform": {"system": "test"}},
    )


def test_canonical_json_ignores_mapping_order_and_decimal_context() -> None:
    value_a = {"z": Decimal("1.2300"), "a": [Decimal("1000.00"), True]}
    value_b = {"a": [Decimal("1000"), True], "z": Decimal("1.23")}

    with localcontext() as context:
        context.prec = 2
        encoded_a = canonical_json(value_a)
    encoded_b = canonical_json(value_b)

    assert encoded_a == encoded_b
    assert canonical_hash(value_a) == canonical_hash(value_b)


def test_experiment_config_detaches_and_deep_freezes_nested_input() -> None:
    source = {"model": {"layers": [{"width": 8}]}}
    config = _config(parameters=source)
    source["model"]["layers"][0]["width"] = 99

    assert config.parameters["model"]["layers"][0]["width"] == 8  # type: ignore[index]
    with pytest.raises(TypeError):
        config.parameters["model"]["layers"][0]["width"] = 10  # type: ignore[index]


def test_manifest_roundtrip_validates_fingerprint_and_is_deeply_immutable() -> None:
    raw_runtime = {"host": {"labels": ["local"]}}
    manifest = replace(_manifest(), runtime_metadata=raw_runtime)
    raw_runtime["host"]["labels"].append("changed")

    restored = ExperimentManifest.from_dict(manifest.to_dict())

    assert restored == manifest
    assert restored.input_fingerprint == manifest.input_fingerprint
    assert restored.runtime_metadata["host"]["labels"] == ("local",)  # type: ignore[index]
    with pytest.raises(TypeError):
        restored.runtime_metadata["host"]["labels"] = ("mutable",)  # type: ignore[index]


def test_input_fingerprint_captures_seed_data_and_execution_settings() -> None:
    baseline = _config()

    assert baseline.input_fingerprint != _config(seed=8).input_fingerprint
    assert baseline.input_fingerprint != replace_dataset_hash(baseline).input_fingerprint
    assert baseline.input_fingerprint != _config_with_changed_commission().input_fingerprint


def test_manifest_input_fingerprint_captures_python_runtime_version() -> None:
    baseline = _manifest()

    assert (
        baseline.input_fingerprint
        != replace(baseline, python_version="Python 3.12.0").input_fingerprint
    )


def replace_dataset_hash(config: ExperimentConfig) -> ExperimentConfig:
    return replace(
        config,
        datasets=(DatasetIdentity("1" * 64, "b" * 64, "c" * 64, 20, ("ETF-1",)),),
    )


def _config_with_changed_commission() -> ExperimentConfig:
    changed = _config().to_dict()
    changed["execution_assumptions"] = {"commission": {"kind": "per_unit", "rate": "0.02"}}
    return ExperimentConfig.from_dict(changed)


def test_exact_result_fingerprint_covers_nav_but_ignores_display_name() -> None:
    baseline = _result()

    assert result_fingerprint(baseline) == result_fingerprint(_result(name="renamed display"))
    assert result_fingerprint(baseline) != result_fingerprint(_result(nav="1009.99"))


@pytest.mark.parametrize("seed", [-1, 2**64, True])
def test_seed_must_be_unsigned_64_bit_integer(seed: int) -> None:
    with pytest.raises(DomainError, match="seed"):
        _config(seed=seed)


def test_canonical_values_reject_floats_and_naive_timestamps() -> None:
    with pytest.raises(DomainError, match="Unsupported"):
        canonical_json({"unsafe_float": 0.1})
    with pytest.raises(DomainError, match="aware"):
        canonical_json({"naive": datetime(2024, 1, 1)})
