"""Real application requests retain M5 economic outputs and verified M8 artifacts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quant_terminal.application.runtime import ResearchRuntime
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta
from quant_terminal.experiments import IntegrityError

PROJECT = Path(__file__).parents[2]
INSTRUMENT = InstrumentMeta(
    InstrumentId("ETF-1"),
    "TEST",
    "USD",
    "etf",
    "XNYS",
    "America/New_York",
    Decimal("0.01"),
    Decimal(1),
)


def setup(tmp_path):
    runtime = ResearchRuntime(tmp_path / "state", PROJECT)
    snapshot = runtime.data.import_csv(
        PROJECT / "examples/data/synthetic_xnys_2026.csv",
        instrument=INSTRUMENT,
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    return runtime, {
        "strategy": "buy_and_hold",
        "parameters": {"instrument_id": "ETF-1", "quantity": "10"},
        "snapshot_ids": [snapshot.snapshot_id],
        "start": "2026-11-25T14:30:00+00:00",
        "end": "2026-11-28T00:00:00+00:00",
        "currency": "USD",
        "initial_capital": "10000",
        "execution": {"commission_per_unit": "0.1"},
        "benchmark": "ETF-1",
    }


def test_request_runs_real_engine_and_repeats_exact_economic_report(tmp_path) -> None:
    runtime, request = setup(tmp_path)
    config = runtime.prepare(request)
    first, result = runtime.run(config, "application-first")
    second, repeated = runtime.run(config, "application-repeat")
    assert result == repeated
    assert first.result_fingerprint == second.result_fingerprint
    assert result.fills and result.commission_cost == Decimal(1)
    report = runtime.read_report(first.run_id.value)
    assert report["strategy_name"] == "buy_and_hold"
    assert report["currency"] == "USD"
    assert runtime.registry.read_artifact(
        first.run_id, "report-bundle.zip"
    ) == runtime.registry.read_artifact(second.run_id, "report-bundle.zip")
    assert runtime.snapshots()[0]["snapshot_id"] == request["snapshot_ids"][0]
    assert {s["name"] for s in runtime.strategies()} == {
        "buy_and_hold",
        "moving_average_cross",
        "simple_momentum",
    }


@pytest.mark.parametrize(
    "strategy,windows",
    [
        ("moving_average_cross", {"fast_period": 1, "slow_period": 2}),
        ("simple_momentum", {"lookback": 1}),
    ],
)
def test_builtin_strategy_catalog_executes(tmp_path, strategy, windows) -> None:
    runtime, request = setup(tmp_path)
    request["strategy"] = strategy
    request["parameters"].update(windows)
    manifest, result = runtime.run(runtime.prepare(request), "builtin-run")
    assert manifest.config.strategy_name == result.strategy_name == strategy
    assert result.nav


def test_strict_request_rejects_unknown_float_boolean_and_unpinned_inputs(tmp_path) -> None:
    runtime, request = setup(tmp_path)
    for update in (
        {"unexpected": True},
        {"initial_capital": 10000.0},
        {"seed": True},
        {"allow_short": "false"},
        {"currency": "GBP"},
        {"benchmark": "UNKNOWN"},
        {"strategy": "exec:module"},
        {"start": "2026-11-25"},
    ):
        with pytest.raises(DomainError):
            runtime.prepare({**request, **update})
    for parameters in (
        {"instrument_id": "ETF-1", "quantity": "NaN"},
        {"instrument_id": "UNKNOWN", "quantity": "10"},
        {"instrument_id": "ETF-1", "quantity": "10", "exec": "code"},
    ):
        with pytest.raises(DomainError):
            runtime.prepare({**request, "parameters": parameters})
    with pytest.raises(DomainError, match="outside"):
        ResearchRuntime(PROJECT / "state", PROJECT)


def test_source_and_dataset_identity_mismatches_stop_before_registration(
    tmp_path, monkeypatch
) -> None:
    runtime, request = setup(tmp_path)
    config = runtime.prepare(request)
    changed = replace(config, datasets=(replace(config.datasets[0], content_hash="a" * 64),))
    with pytest.raises(IntegrityError, match="Dataset identity"):
        runtime.run(changed, "bad-data")
    monkeypatch.setattr(
        "quant_terminal.application.runtime.build_source_bundle", lambda root: b"different"
    )
    with pytest.raises(IntegrityError, match="Executing source"):
        runtime.run(config, "bad-source")


def test_report_read_rejects_changed_artifact(tmp_path) -> None:
    runtime, request = setup(tmp_path)
    manifest, _ = runtime.run(runtime.prepare(request), "tamper-run")
    # Corrupt an immutable artifact to exercise M8 checks through the public query.
    artifact = next((runtime.root / "runs").rglob("report-bundle.zip"))
    artifact.write_bytes(b"corrupted")
    with pytest.raises(IntegrityError):
        runtime.read_report(manifest.run_id.value)


def test_source_is_rechecked_after_execution_and_zero_costs_require_label(
    tmp_path, monkeypatch
) -> None:
    runtime, request = setup(tmp_path)
    with pytest.raises(DomainError, match="Zero-cost"):
        runtime.prepare({**request, "execution": {"commission_per_unit": "0"}})
    config = runtime.prepare(request)
    from quant_terminal.application import runtime as runtime_module

    original = runtime_module.build_source_bundle
    calls = []

    def changing_source(root):
        calls.append(root)
        return original(root) if len(calls) == 1 else b"changed during execution"

    monkeypatch.setattr(runtime_module, "build_source_bundle", changing_source)
    with pytest.raises(IntegrityError, match="Executing source"):
        runtime.run(config, "mid-run-change")
    assert len(calls) == 2


def boundary_request(tmp_path, *, bars=3, final_volume=100):
    runtime = ResearchRuntime(tmp_path / "state", PROJECT)
    start = datetime(2026, 11, 25, 14, 30, tzinfo=UTC)
    lines = ["instrument_id,frequency,start,end,available_at,open,high,low,close,volume,source"]
    for index in range(bars):
        begin = start + timedelta(minutes=index)
        close_at = begin + timedelta(minutes=1)
        opening = 100 + index
        closing = 110 if index == bars - 1 else opening + 1
        volume = final_volume if index == bars - 1 else 100
        lines.append(
            f"ETF-1,1m,{begin.isoformat()},{close_at.isoformat()},{close_at.isoformat()},"
            f"{opening},{closing},{opening},{closing},{volume},synthetic-fixture"
        )
    csv = tmp_path / "boundary.csv"
    csv.write_text("\n".join(lines), encoding="utf-8")
    snapshot = runtime.data.import_csv(csv, instrument=INSTRUMENT, source="synthetic-fixture")
    return runtime, {
        "strategy": "buy_and_hold",
        "parameters": {"instrument_id": "ETF-1", "quantity": "10"},
        "snapshot_ids": [snapshot.snapshot_id],
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=bars + 1)).isoformat(),
        "currency": "USD",
        "initial_capital": "10000",
        "boundary_liquidation": True,
        "execution": {
            "commission_per_unit": "0.1",
            "slippage_kind": "fixed",
            "slippage_value": "0.05",
            "maximum_participation": "0.2",
        },
    }


def test_boundary_executes_actual_final_close_costs_and_flat_nav(tmp_path) -> None:
    runtime, request = boundary_request(tmp_path)
    manifest, result = runtime.run(runtime.prepare(request), "boundary")
    assert len(result.fills) == 2
    entry, close = result.fills
    assert entry.reference_price == Decimal(101)
    assert close.reference_price == Decimal(110)  # Final bar OPEN is 102.
    assert close.price == Decimal("109.95")
    assert close.commission == Decimal(1)
    assert close.timestamp == close.observed_at == datetime(2026, 11, 25, 14, 33, tzinfo=UTC)
    assert result.nav[-1].nav == Decimal(10087)
    assert all(position.quantity == 0 for position in result.nav[-1].positions)
    assert all(order.remaining_quantity == 0 for order in result.orders)
    assert dict(result.metadata)["boundary_liquidation"] == "scheduled_final_close_v1"
    assert manifest.config.backtest_settings["boundary_liquidation"] is True


@pytest.mark.parametrize("bars,volume", [(3, 10), (2, 50)])
def test_boundary_rejects_low_capacity_including_prior_same_bar_fills(
    tmp_path, bars, volume
) -> None:
    runtime, request = boundary_request(tmp_path, bars=bars, final_volume=volume)
    # Two bars: regular entry consumes the full 10-unit final-bar allowance;
    # liquidation must not receive a second independent allowance.
    with pytest.raises(DomainError, match="remaining final-bar"):
        runtime.run(runtime.prepare(request), "no-capacity")


def test_boundary_rejects_delayed_final_publication(tmp_path) -> None:
    runtime, request = setup(tmp_path)
    request["boundary_liquidation"] = True
    with pytest.raises(DomainError, match="immediate publication"):
        runtime.run(runtime.prepare(request), "delayed-close")
