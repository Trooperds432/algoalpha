"""Real M5/M8 robustness reruns and delayed-observation causality checks."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from test_experiments import (
    END,
    INSTRUMENT,
    PROJECT,
    START,
    STRATEGY_SOURCE,
    _manifest,
    _outcome,
)

from quant_terminal.analytics import AnalyticsConfig
from quant_terminal.backtesting.engine import BacktestEngine
from quant_terminal.backtesting.models import BacktestConfig
from quant_terminal.backtesting.strategies import BuyAndHold
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.data.storage import DatasetStore
from quant_terminal.execution.models import ExecutionConfig, PerUnitCommission
from quant_terminal.experiments import (
    DatasetIdentity,
    ExperimentStore,
    IntegrityError,
    build_source_bundle,
)
from quant_terminal.robustness.adapter import ObservationOverlay, ResearchScenarioAdapter
from quant_terminal.robustness.engine import ScenarioExecutionError, run_robustness
from quant_terminal.robustness.models import RobustnessScenario, RobustnessSpec, ScenarioKind


def _setup(tmp_path: Path, two=True):
    source = {
        "source-bundle.zip": build_source_bundle(PROJECT),
        "strategy-source.py": STRATEGY_SOURCE.read_bytes(),
    }
    data = DatasetStore(tmp_path / "data")
    snapshots = []
    for instrument_id in ("ETF-1", "ETF-2") if two else ("ETF-1",):
        instrument = replace(INSTRUMENT, instrument_id=InstrumentId(instrument_id))
        lines = ["instrument_id,frequency,start,end,available_at,open,high,low,close,volume,source"]
        for index in range(10):
            begin = START + timedelta(minutes=index)
            end = begin + timedelta(minutes=1)
            price = 100 + index
            lines.append(
                f"{instrument_id},1m,{begin.isoformat()},{end.isoformat()},{end.isoformat()},"
                f"{price},{price + 1},{price},{price + 1},1000,synthetic-fixture"
            )
        csv = tmp_path / f"{instrument_id}.csv"
        csv.write_text("\n".join(lines), encoding="utf-8")
        snapshots.append(data.import_csv(csv, instrument=instrument, source="synthetic-fixture"))
    baseline_result = BacktestEngine(
        data,
        snapshots,
        BuyAndHold(INSTRUMENT.instrument_id, Decimal(10)),
        BacktestConfig(Decimal(10000), "USD", max_order_notional=Decimal(1000000)),
        ExecutionConfig(commission=PerUnitCommission(Decimal("0.1"))),
    ).run(start=START, end=END, benchmark=INSTRUMENT.instrument_id)
    baseline = _manifest(snapshots[0], baseline_result, source)
    baseline = replace(
        baseline,
        config=replace(
            baseline.config,
            datasets=tuple(
                DatasetIdentity(
                    s.snapshot_id,
                    s.content_hash,
                    s.input_hash,
                    s.row_count,
                    (s.instrument.instrument_id.value,),
                )
                for s in snapshots
            ),
        ),
    )
    registry = ExperimentStore(tmp_path / "runs")
    baseline = registry.create(
        baseline,
        _outcome(baseline, baseline_result, source),
        {
            **source,
            "report-bundle.zip": registry._report_bytes(baseline_result, AnalyticsConfig()),
        },
    )
    return data, registry, baseline, snapshots


def _adapter(data, registry, baseline, namespace="first"):
    return ResearchScenarioAdapter(
        registry,
        data,
        baseline,
        lambda config: BuyAndHold(
            INSTRUMENT.instrument_id, Decimal(str(config.parameters["quantity"]))
        ),
        run_namespace=namespace,
        project_root=PROJECT,
        strategy_source=STRATEGY_SOURCE,
    )


def test_real_scenarios_retain_costs_dates_regimes_universe_and_failures(tmp_path: Path) -> None:
    data, registry, baseline, _ = _setup(tmp_path)
    scenarios = (
        RobustnessScenario(
            "quantity", ScenarioKind.PARAMETER, (("parameters.quantity", 5),), "smaller size"
        ),
        RobustnessScenario(
            "fees",
            ScenarioKind.COMMISSION,
            (("execution_assumptions.commission.per_unit_rate", Decimal("0.2")),),
            "double fees",
        ),
        RobustnessScenario(
            "slippage",
            ScenarioKind.SLIPPAGE,
            (
                ("execution_assumptions.slippage.kind", "fixed"),
                ("execution_assumptions.slippage.value", Decimal("0.05")),
            ),
            "adverse fills",
        ),
        RobustnessScenario(
            "later",
            ScenarioKind.START_DATE,
            (("start", START + timedelta(minutes=2)),),
            "later start",
        ),
        RobustnessScenario(
            "earlier",
            ScenarioKind.END_DATE,
            (("end", START + timedelta(minutes=8)),),
            "earlier end",
        ),
        RobustnessScenario(
            "period",
            ScenarioKind.SUBPERIOD,
            (("start", START + timedelta(minutes=1)), ("end", START + timedelta(minutes=9))),
            "short window",
        ),
        RobustnessScenario(
            "regime",
            ScenarioKind.REGIME,
            (
                ("regime.start", START),
                ("regime.end", START + timedelta(minutes=8)),
                ("regime.label", "synthetic rising prices"),
            ),
            "declared regime",
        ),
        RobustnessScenario(
            "universe",
            ScenarioKind.UNIVERSE,
            (("universe.exclude", ("ETF-1",)),),
            "drop traded asset",
        ),
        RobustnessScenario(
            "invalid",
            ScenarioKind.UNIVERSE,
            (("universe.include", ("MISSING",)),),
            "missing instrument",
        ),
        RobustnessScenario(
            "factory-failure",
            ScenarioKind.PARAMETER,
            (("parameters.quantity", 1),),
            "failure fixture",
        ),
    )
    spec = RobustnessSpec("sensitivity", baseline, scenarios)
    adapter = _adapter(data, registry, baseline)
    log = tmp_path / "scenario-journal.jsonl"

    def recorder(row):
        with log.open("ab") as stream:
            stream.write(row + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def evaluate(scenario, config):
        if scenario.scenario_id == "factory-failure":
            raise ScenarioExecutionError("deliberate local failure")
        return adapter(scenario, config)

    result = run_robustness(spec, registry, evaluate, recorder)
    rows = [json.loads(row) for row in result.rows]
    assert result.completed and len(rows) == len(scenarios)
    assert [row["status"] for row in rows] == ["completed"] * 8 + ["invalid", "failed"]
    assert len(log.read_bytes().splitlines()) == len(scenarios)
    assert dict(registry.get(rows[1]["manifest"]["run_id"]).metrics)[
        "commissions"
    ].value == Decimal(2)
    assert dict(registry.get(rows[2]["manifest"]["run_id"]).metrics)[
        "slippage_cost"
    ].value == Decimal("0.5")
    assert dict(registry.get(rows[7]["manifest"]["run_id"]).metrics)["commissions"].value == 0
    assert json.loads(result.artifact_bytes())["completed"] is True
    repeated = run_robustness(
        spec, registry, _adapter(data, registry, baseline, "repeat"), lambda row: None
    )
    repeat_rows = [json.loads(row) for row in repeated.rows]
    for left, right in zip(rows[:8], repeat_rows[:8], strict=True):
        assert left["manifest"]["result_fingerprint"] == right["manifest"]["result_fingerprint"]
        assert registry.read_artifact(
            left["manifest"]["run_id"], "report-bundle.zip"
        ) == registry.read_artifact(right["manifest"]["run_id"], "report-bundle.zip")


def test_delayed_observations_change_fill_time_and_withhold_newer_history(tmp_path: Path) -> None:
    data, registry, baseline, snapshots = _setup(tmp_path, two=False)
    one = snapshots[:1]
    observed = []

    class Probe(BuyAndHold):
        def on_bar(self, context, bar):
            history = context.history(self.instrument_id, 100)
            observed.append(
                (bar.available_at, context.current_time, tuple(b.available_at for b in history))
            )
            return super().on_bar(context, bar)

    wrapper = ObservationOverlay(Probe(INSTRUMENT.instrument_id, Decimal(10)), 2, ("ETF-1",))
    delayed = BacktestEngine(
        data,
        one,
        wrapper,
        BacktestConfig(Decimal(10000), "USD"),
        ExecutionConfig(commission=PerUnitCommission(Decimal("0.1"))),
    ).run(start=START, end=END)
    assert delayed.fills[0].timestamp == START + timedelta(minutes=3)
    assert delayed.fills[0].reference_price == Decimal(103)
    assert all(
        current > original and max(history) <= original for original, current, history in observed
    )
    assert len(wrapper.pending) == 2
    scenarios = (
        RobustnessScenario("valid", ScenarioKind.BAR_DELAY, (("bar_delay", 2),), "delayed signal"),
        RobustnessScenario(
            "no-fill", ScenarioKind.BAR_DELAY, (("bar_delay", 9),), "no executable bar"
        ),
        RobustnessScenario(
            "too-long", ScenarioKind.BAR_DELAY, (("bar_delay", 100),), "no observations"
        ),
    )
    rows = []
    result = run_robustness(
        RobustnessSpec("delays", baseline, scenarios),
        registry,
        _adapter(data, registry, baseline),
        rows.append,
    )
    assert result.completed
    assert [json.loads(row)["status"] for row in rows] == ["completed", "failed", "failed"]
    run_id = json.loads(rows[0])["manifest"]["run_id"]
    overlay = json.loads(registry.read_artifact(run_id, "observation-overlay.json"))
    assert overlay["undelivered_tail"] == 2


def test_integrity_failure_is_recorded_and_stops_campaign(tmp_path: Path) -> None:
    data, registry, baseline, _ = _setup(tmp_path)
    scenario = RobustnessScenario(
        "quantity", ScenarioKind.PARAMETER, (("parameters.quantity", 5),), "neighbor"
    )
    records = []
    with pytest.raises(IntegrityError, match="unique"):
        run_robustness(
            RobustnessSpec("bad", baseline, (scenario,)),
            registry,
            lambda s, c: baseline,
            records.append,
        )
    assert len(records) == 1 and json.loads(records[0])["status"] == "integrity_failed"


def test_changed_executing_source_stops_before_factory(tmp_path: Path, monkeypatch) -> None:
    data, registry, baseline, _ = _setup(tmp_path)
    scenario = RobustnessScenario(
        "source-check", ScenarioKind.PARAMETER, (("parameters.quantity", 5),), "source fixture"
    )
    monkeypatch.setattr(
        "quant_terminal.robustness.adapter.build_source_bundle", lambda root: b"changed source"
    )
    records = []
    with pytest.raises(IntegrityError, match="project source changed"):
        run_robustness(
            RobustnessSpec("changed", baseline, (scenario,)),
            registry,
            _adapter(data, registry, baseline),
            records.append,
        )
    assert json.loads(records[0])["status"] == "integrity_failed"


def test_factory_failure_is_local_and_recorder_failure_stops(tmp_path: Path) -> None:
    data, registry, baseline, _ = _setup(tmp_path)
    scenarios = tuple(
        RobustnessScenario(
            f"quantity-{quantity}",
            ScenarioKind.PARAMETER,
            (("parameters.quantity", quantity),),
            "factory fixture",
        )
        for quantity in (1, 5)
    )

    def factory(config):
        if config.parameters["quantity"] == 1:
            raise ValueError("deliberate factory error")
        return BuyAndHold(INSTRUMENT.instrument_id, Decimal(str(config.parameters["quantity"])))

    adapter = ResearchScenarioAdapter(
        registry,
        data,
        baseline,
        factory,
        run_namespace="factory",
        project_root=PROJECT,
        strategy_source=STRATEGY_SOURCE,
    )
    spec = RobustnessSpec("factory", baseline, scenarios)
    records = []
    result = run_robustness(spec, registry, adapter, records.append)
    assert result.completed
    assert [json.loads(row)["status"] for row in records] == ["failed", "completed"]
    assert "factory failed" in json.loads(records[0])["reason"]
    visits = []

    def evaluator(scenario, config):
        visits.append(scenario.scenario_id)
        raise ScenarioExecutionError("local failure")

    def broken_recorder(row):
        raise OSError("journal unavailable")

    with pytest.raises(OSError, match="journal unavailable"):
        run_robustness(spec, registry, evaluator, broken_recorder)
    assert visits == [scenarios[0].scenario_id]
    cancelled = run_robustness(spec, registry, evaluator, records.append, cancelled=lambda: True)
    assert not cancelled.completed and cancelled.rows == ()
