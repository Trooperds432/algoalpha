"""Owner release acceptance: one unchanged strategy through research and paper."""

from __future__ import annotations

import hashlib
import zipfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from quant_terminal.application.campaigns import ResearchCampaigns
from quant_terminal.application.runtime import ResearchRuntime
from quant_terminal.application.service import ResearchService
from quant_terminal.core.identifiers import InstrumentId, RunId
from quant_terminal.data.model import Bar, InstrumentMeta
from quant_terminal.paper.clock import LiveClock
from quant_terminal.paper.engine import PaperSession
from quant_terminal.paper.schema import config as paper_config

PROJECT = Path(__file__).resolve().parents[2]
START = datetime(2026, 11, 25, 14, 30, tzinfo=UTC)
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


def _dataset(tmp_path: Path, runtime: ResearchRuntime) -> tuple[str, tuple[Bar, ...]]:
    bars = tuple(
        Bar(
            INSTRUMENT.instrument_id,
            "1m",
            START + timedelta(minutes=index),
            START + timedelta(minutes=index + 1),
            START + timedelta(minutes=index + 1),
            Decimal(100 + index),
            Decimal(102 + index),
            Decimal(100 + index),
            Decimal(101 + index),
            Decimal(1000),
            "synthetic-lifecycle-fixture",
        )
        for index in range(12)
    )
    csv_path = tmp_path / "lifecycle.csv"
    lines = ["instrument_id,frequency,start,end,available_at,open,high,low,close,volume,source"]
    for item in bars:
        lines.append(
            f"{item.instrument_id.value},{item.frequency},{item.start.isoformat()},"
            f"{item.end.isoformat()},{item.available_at.isoformat()},{item.open},{item.high},"
            f"{item.low},{item.close},{item.volume},{item.source}"
        )
    csv_path.write_text("\n".join(lines), encoding="utf-8")
    snapshot = runtime.data.import_csv(
        csv_path, instrument=INSTRUMENT, source="synthetic-lifecycle-fixture"
    )
    return snapshot.snapshot_id, bars


def _backtest_request(snapshot_id: str) -> dict[str, object]:
    return {
        "strategy": "buy_and_hold",
        "parameters": {"instrument_id": "ETF-1", "quantity": "10"},
        "snapshot_ids": [snapshot_id],
        "start": START.isoformat(),
        "end": (START + timedelta(minutes=12)).isoformat(),
        "currency": "USD",
        "initial_capital": "10000",
        "benchmark": "ETF-1",
        "execution": {"commission_per_unit": "0.1"},
    }


def _paper_request() -> dict[str, object]:
    return {
        "instrument": {
            "instrument_id": "ETF-1",
            "symbol": "TEST",
            "currency": "USD",
            "asset_class": "etf",
            "calendar": "XNYS",
            "timezone": "America/New_York",
            "tick_size": "0.01",
            "quantity_step": "1",
        },
        "strategy": "buy_and_hold",
        "parameters": {"quantity": "10"},
        "initial_cash": "10000",
        "max_order_notional": "2000",
        "execution": {"commission_per_unit": "0.1"},
        "stale_seconds": 120,
    }


def test_same_builtin_strategy_completes_research_and_paper_lifecycle(tmp_path: Path) -> None:
    runtime = ResearchRuntime(tmp_path / "runtime", PROJECT)
    snapshot_id, bars = _dataset(tmp_path, runtime)
    request = _backtest_request(snapshot_id)
    config = runtime.prepare(request)
    source_digest = hashlib.sha256(runtime.strategy_source.read_bytes()).hexdigest()
    assert config.strategy_source_hash == source_digest
    historical_strategy = runtime.strategy(config)

    backtest_manifest, backtest_result = runtime.run(config, "m21-backtest")
    assert backtest_manifest.config.strategy_source_hash == source_digest
    assert backtest_result.strategy_name == "buy_and_hold"
    assert [fill.quantity for fill in backtest_result.fills] == [Decimal(10)]
    assert [fill.price for fill in backtest_result.fills] == [Decimal(101)]
    assert [fill.commission for fill in backtest_result.fills] == [Decimal(1)]
    assert backtest_result.commission_cost == Decimal(1)
    assert backtest_result.slippage_cost == Decimal(0)
    # Entry at 101, final mark 112, ten shares, one one-dollar fee.
    assert backtest_result.nav[-1].nav == Decimal(10109)
    report = runtime.read_report(backtest_manifest.run_id.value)
    assert report["strategy_name"] == "buy_and_hold"
    assert report["currency"] == "USD"
    report_bundle = runtime.registry.read_artifact(backtest_manifest.run_id, "report-bundle.zip")
    with zipfile.ZipFile(BytesIO(report_bundle)) as archive:
        html = archive.read("report.html")
    assert b"<html" in html.lower()
    assert b"buy_and_hold" in html

    campaigns = ResearchCampaigns(runtime)
    optimized = campaigns.optimize(
        {
            "backtest": request,
            "dimensions": [{"name": "quantity", "choices": ["5", "10"]}],
            "method": "grid",
            "candidate_budget": 2,
            "seed": 1,
            "objective_metric": "total_return",
            "direction": "maximize",
        },
        "m21-optimize",
        lambda _event: None,
    )
    trial_ids = tuple(str(trial["run_id"]) for trial in optimized["trials"])
    assert optimized["completed"] is True
    assert len(trial_ids) == 2
    assert all(
        runtime.registry.get(RunId(identity)).config.strategy_name == "buy_and_hold"
        for identity in trial_ids
    )
    assert optimized["selected_strategy"] is None

    walk_forward = campaigns.walk_forward(
        {
            "backtest": request,
            "dimensions": [{"name": "quantity", "choices": ["10"]}],
            "method": "grid",
            "candidate_budget": 1,
            "seed": 2,
            "objective_metric": "total_return",
            "direction": "maximize",
            "training_seconds": 240,
            "test_seconds": 240,
            "mode": "rolling",
        },
        "m21-walk-forward",
        lambda _event: None,
    )
    windows = walk_forward["windows"]
    assert walk_forward["completed"] is True
    assert len(windows) == 2
    assert [row["window"]["index"] for row in windows] == [0, 1]
    stitched = walk_forward["stitched_oos_equity"]
    assert stitched
    assert {point["window_index"] for point in stitched} == {0, 1}
    oos_run_ids = tuple(str(row["oos_run"]["run_id"]) for row in windows)
    assert all(
        runtime.registry.get(RunId(identity)).config.strategy_name == "buy_and_hold"
        for identity in oos_run_ids
    )
    assert all(
        row["oos_run"]["config"]["backtest_settings"]["boundary_liquidation"] for row in windows
    )

    monte_carlo = campaigns.monte_carlo(
        {
            "source_run_ids": list(oos_run_ids),
            "method": "iid_return_bootstrap",
            "sample_kind": "periodic_return",
            "path_count": 8,
            "path_length": 2,
            "seed": 9,
            "periods_per_year": 252,
            "currency": "USD",
            "initial_equity": "10000",
        },
        "m21-monte-carlo",
        lambda _event: None,
    )
    assert (
        monte_carlo["result"]["sample_fingerprint"]
        == monte_carlo["specification"]["sample_fingerprint"]
    )
    assert tuple(monte_carlo["specification"]["source_run_ids"]) == oos_run_ids

    robustness = campaigns.robustness(
        {
            "baseline_run_id": backtest_manifest.run_id.value,
            "scenarios": [
                {
                    "scenario_id": "higher-commission",
                    "kind": "commission",
                    "changes": {"execution_assumptions.commission.per_unit_rate": "0.2"},
                    "rationale": "Synthetic execution cost stress",
                }
            ],
        },
        "m21-robustness",
        lambda _event: None,
    )
    assert robustness["completed"] is True
    assert robustness["scenarios"][0]["status"] == "completed"

    service = ResearchService(runtime.root, PROJECT)
    comparison = service.compare([backtest_manifest.run_id.value, trial_ids[0]])
    assert comparison["warnings"] == []
    assert set(comparison["run_ids"]) == {backtest_manifest.run_id.value, trial_ids[0]}
    assert service.html_report(backtest_manifest.run_id.value) == html

    paper_payload = _paper_request()
    paper_strategy = paper_config(paper_payload).strategy
    assert type(paper_strategy) is type(historical_strategy)
    assert paper_strategy.name == historical_strategy.name == "buy_and_hold"
    assert paper_strategy.quantity == historical_strategy.quantity == Decimal(10)
    clock = [bars[0].end]
    paper = PaperSession.create(
        tmp_path / "paper.sqlite3", paper_payload, clock=LiveClock(lambda: clock[0])
    )
    assert paper.snapshot()["broker_orders_enabled"] is False
    paper.reconnect("m21-reconnect", paper.snapshot()["reconciliation"])
    for sequence, bar in enumerate(bars, start=1):
        clock[0] = bar.end
        paper.ingest(f"m21-bar-{sequence}", sequence, bar)
    paper_result = paper.snapshot()
    paper_fills = paper_result["orders"][0]["fills"]
    assert len(paper_fills) == 1
    assert paper_fills[0]["side"] == "buy"
    assert paper_fills[0]["quantity"] == "10"
    assert paper_fills[0]["price"] == "101"
    assert paper_fills[0]["commission"] == "1"
    assert paper_result["portfolio"]["positions"][0]["quantity"] == "10"
    assert paper_result["portfolio"]["cash"] == "8989"
    assert paper_result["portfolio"]["nav"] == "10109"
    assert hashlib.sha256(runtime.strategy_source.read_bytes()).hexdigest() == source_digest
