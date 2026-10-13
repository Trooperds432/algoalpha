"""Real runtime integration for optimization, source-derived Monte Carlo, and robustness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from quant_terminal.application.campaigns import ResearchCampaigns
from quant_terminal.application.runtime import ResearchRuntime
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.model import InstrumentMeta

PROJECT = Path(__file__).parents[2]
DATA = PROJECT / "examples" / "data" / "synthetic_xnys_2026.csv"


@pytest.fixture
def research(tmp_path: Path) -> tuple[ResearchRuntime, ResearchCampaigns, dict[str, object]]:
    runtime = ResearchRuntime(tmp_path / "runtime", PROJECT)
    snapshot = runtime.data.import_csv(
        DATA,
        instrument=InstrumentMeta(
            InstrumentId("ETF-1"),
            "TEST",
            "USD",
            "etf",
            "XNYS",
            "America/New_York",
            Decimal("0.01"),
            Decimal(1),
        ),
        source="synthetic-fixture",
        publication_delay=timedelta(minutes=1),
    )
    backtest: dict[str, object] = {
        "strategy": "buy_and_hold",
        "parameters": {"instrument_id": "ETF-1", "quantity": "10"},
        "snapshot_ids": [snapshot.snapshot_id],
        "start": "2026-11-25T14:30:00Z",
        "end": "2026-11-28T00:00:00Z",
        "currency": "USD",
        "initial_capital": "10000",
        "execution": {"commission_per_unit": "0.1"},
    }
    return runtime, ResearchCampaigns(runtime), backtest


def test_real_optimization_records_every_candidate_and_m8_identity(research) -> None:
    _, campaigns, backtest = research
    records: list[dict[str, object]] = []
    result = campaigns.optimize(
        {
            "backtest": backtest,
            "dimensions": [{"name": "quantity", "choices": ["5", "10"]}],
            "method": "grid",
            "candidate_budget": 2,
            "seed": 17,
            "objective_metric": "total_return",
            "direction": "maximize",
        },
        "job-optimize-1",
        records.append,
    )
    assert result["completed"] is True
    assert len(result["trials"]) == 2
    assert [item["kind"] for item in records] == [
        "optimization_specification",
        "optimization_trial",
        "optimization_trial",
    ]
    trials = result["trials"]
    assert all(item["run_id"] and item["result_fingerprint"] for item in trials)
    assert result["selected_strategy"] is None


def test_monte_carlo_uses_verified_report_rows_and_rejects_client_returns(research) -> None:
    _, campaigns, backtest = research
    optimized = campaigns.optimize(
        {
            "backtest": backtest,
            "dimensions": [{"name": "quantity", "choices": ["10"]}],
            "method": "grid",
            "candidate_budget": 1,
            "seed": 1,
            "objective_metric": "total_return",
            "direction": "maximize",
        },
        "job-source-run",
        lambda _: None,
    )
    run_id = optimized["trials"][0]["run_id"]
    records: list[dict[str, object]] = []
    report = campaigns.monte_carlo(
        {
            "source_run_ids": [run_id],
            "method": "iid_return_bootstrap",
            "sample_kind": "periodic_return",
            "path_count": 8,
            "path_length": 2,
            "seed": 9,
            "periods_per_year": 252,
            "currency": "USD",
            "initial_equity": "10000",
        },
        "job-mc-1",
        records.append,
    )
    assert report["result"]["sample_fingerprint"] == report["specification"]["sample_fingerprint"]
    assert records[0]["kind"] == "monte_carlo_result"
    with pytest.raises(DomainError):
        campaigns.monte_carlo(
            {
                "source_run_ids": [run_id],
                "returns": ["0.2"],
                "method": "iid_return_bootstrap",
                "sample_kind": "periodic_return",
                "path_count": 1,
                "path_length": 1,
                "seed": 1,
                "periods_per_year": 252,
                "currency": "USD",
                "initial_equity": "10000",
            },
            "job-mc-invalid",
            lambda _: None,
        )


def test_robustness_replays_registered_baseline_and_retains_scenario(research) -> None:
    _, campaigns, backtest = research
    optimized = campaigns.optimize(
        {
            "backtest": backtest,
            "dimensions": [{"name": "quantity", "choices": ["10"]}],
            "method": "grid",
            "candidate_budget": 1,
            "seed": 1,
            "objective_metric": "total_return",
            "direction": "maximize",
        },
        "job-robust-base",
        lambda _: None,
    )
    baseline_id = optimized["trials"][0]["run_id"]
    records: list[dict[str, object]] = []
    report = campaigns.robustness(
        {
            "baseline_run_id": baseline_id,
            "scenarios": [
                {
                    "scenario_id": "higher-fees",
                    "kind": "commission",
                    "changes": {"execution_assumptions.commission.per_unit_rate": "0.2"},
                    "rationale": "Cost stress",
                }
            ],
        },
        "job-robust-1",
        records.append,
    )
    assert report["completed"] is True
    assert report["scenarios"][0]["status"] == "completed"
    assert records[0]["kind"] == "robustness_scenario"


def test_walk_forward_runs_multiple_real_windows_and_stitches_economic_equity(
    tmp_path: Path,
) -> None:
    runtime = ResearchRuntime(tmp_path / "runtime-wf", PROJECT)
    start = datetime(2026, 11, 25, 14, 30, tzinfo=UTC)
    lines = ["instrument_id,frequency,start,end,available_at,open,high,low,close,volume,source"]
    for index in range(12):
        begin = start + timedelta(minutes=index)
        end = begin + timedelta(minutes=1)
        opening = Decimal(100 + index)
        closing = opening + Decimal(1)
        lines.append(
            f"ETF-1,1m,{begin.isoformat()},{end.isoformat()},{end.isoformat()},"
            f"{opening},{closing},{opening},{closing},1000,synthetic-fixture"
        )
    csv_path = tmp_path / "walk-forward.csv"
    csv_path.write_text("\n".join(lines), encoding="utf-8")
    snapshot = runtime.data.import_csv(
        csv_path,
        instrument=InstrumentMeta(
            InstrumentId("ETF-1"),
            "TEST",
            "USD",
            "etf",
            "XNYS",
            "America/New_York",
            Decimal("0.01"),
            Decimal(1),
        ),
        source="synthetic-fixture",
    )
    campaigns = ResearchCampaigns(runtime)
    backtest: dict[str, object] = {
        "strategy": "buy_and_hold",
        "parameters": {"instrument_id": "ETF-1", "quantity": "10"},
        "snapshot_ids": [snapshot.snapshot_id],
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=12)).isoformat(),
        "currency": "USD",
        "initial_capital": "10000",
        "execution": {"commission_per_unit": "0.1"},
    }
    wf_payload = {
        "backtest": backtest,
        "dimensions": [{"name": "quantity", "choices": ["10"]}],
        "method": "grid",
        "candidate_budget": 1,
        "seed": 1,
        "objective_metric": "total_return",
        "direction": "maximize",
        "training_seconds": 240,
        "test_seconds": 240,
        "mode": "rolling",
    }
    records: list[dict[str, object]] = []
    report = campaigns.walk_forward(wf_payload, "job-wf-1", records.append)
    assert report["completed"] is True
    windows = report["windows"]
    assert len(windows) == 2
    assert [row["window"]["index"] for row in windows] == [0, 1]
    assert all(row["optimization_trials"] for row in windows)
    assert all(
        row["oos_run"]["config"]["backtest_settings"]["boundary_liquidation"] for row in windows
    )
    stitched = report["stitched_oos_equity"]
    assert {point["window_index"] for point in stitched} == {0, 1}
    assert any(event["kind"] == "walk_forward_window" for event in records)
