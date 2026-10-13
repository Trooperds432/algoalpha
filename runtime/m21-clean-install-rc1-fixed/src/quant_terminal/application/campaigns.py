"""Application adapters joining verified runtime runs to research engines."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import Enum
from typing import TYPE_CHECKING, cast

from quant_terminal.analytics import Metric
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import RunId
from quant_terminal.experiments import (
    ExperimentConfig,
    ExperimentManifest,
    StoreError,
    canonical_hash,
    canonical_json,
)
from quant_terminal.monte_carlo import BootstrapMethod, MonteCarloSpec, SampleKind, run_simulation
from quant_terminal.optimization import (
    Bound,
    Direction,
    MetricConstraint,
    OptimizationResult,
    OptimizationSpec,
    ParameterDimension,
    SearchMethod,
    TrialEvaluation,
    TrialExecutionError,
    run_optimization,
)
from quant_terminal.optimization.engine import OptimizationTrial
from quant_terminal.robustness.adapter import ResearchScenarioAdapter
from quant_terminal.robustness.engine import run_robustness
from quant_terminal.robustness.models import RobustnessScenario, RobustnessSpec, ScenarioKind
from quant_terminal.walk_forward import (
    FrozenSelection,
    OOSRun,
    TrainingContext,
    WalkForwardSpec,
    WalkForwardWindow,
    WalkForwardWindowResult,
    WindowMode,
    run_walk_forward,
)

if TYPE_CHECKING:
    from quant_terminal.application.runtime import ResearchRuntime

Record = Callable[[dict[str, object]], None]


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise DomainError(f"{name} must be an object with string keys")
    return value


def _keys(data: Mapping[str, object], required: set[str], optional: set[str], name: str) -> None:
    if not required <= set(data) or set(data) - required - optional:
        raise DomainError(f"{name} has missing or unknown fields")


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError(f"{name} must be a nonempty string")
    return value


def _decimal(value: object, name: str) -> Decimal:
    if not isinstance(value, str):
        raise DomainError(f"{name} must be an exact decimal string")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise DomainError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise DomainError(f"{name} must be finite")
    return result


def _int(value: object, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise DomainError(f"{name} must be an integer >= {minimum}")
    return value


def _date(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise DomainError(f"{name} must be an ISO-8601 timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DomainError(f"{name} must be an ISO-8601 timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise DomainError(f"{name} must include a UTC offset")
    return result.astimezone(UTC)


def _json(value: object) -> object:
    def project(item: object) -> object:
        if is_dataclass(item) and not isinstance(item, type):
            return {field.name: project(getattr(item, field.name)) for field in fields(item)}
        if isinstance(item, Mapping):
            return {str(key): project(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [project(child) for child in item]
        if isinstance(item, Enum):
            return item.value
        return item

    return json.loads(canonical_json(project(value)))


def _config_with(
    config: ExperimentConfig,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    initial_capital: Decimal | None = None,
    parameters: Mapping[str, object] | None = None,
) -> ExperimentConfig:
    return replace(
        config,
        start=config.start if start is None else start,
        end=config.end if end is None else end,
        initial_capital=config.initial_capital if initial_capital is None else initial_capital,
        parameters=config.parameters if parameters is None else parameters,
    )


def _dimensions(payload: Mapping[str, object]) -> tuple[ParameterDimension, ...]:
    raw = payload.get("dimensions")
    if not isinstance(raw, (list, tuple)) or not raw:
        raise DomainError("dimensions must be a nonempty array")
    result: list[ParameterDimension] = []
    for index, item in enumerate(raw):
        row = _mapping(item, f"dimensions[{index}]")
        _keys(row, {"name", "choices"}, set(), f"dimensions[{index}]")
        choices = row["choices"]
        if not isinstance(choices, (list, tuple)):
            raise DomainError("dimension choices must be an array")
        result.append(ParameterDimension(_text(row["name"], "dimension name"), tuple(choices)))
    return tuple(result)


def _optimization_spec(
    payload: Mapping[str, object], config: ExperimentConfig, campaign_id: str
) -> OptimizationSpec:
    method = SearchMethod(_text(payload.get("method"), "method"))
    direction = Direction(_text(payload.get("direction"), "direction"))
    constraints: list[MetricConstraint] = []
    raw_constraints = payload.get("constraints", ())
    if not isinstance(raw_constraints, (list, tuple)):
        raise DomainError("constraints must be an array")
    for index, item in enumerate(raw_constraints):
        row = _mapping(item, f"constraints[{index}]")
        _keys(row, {"metric", "bound", "threshold"}, set(), f"constraints[{index}]")
        constraints.append(
            MetricConstraint(
                _text(row["metric"], "constraint metric"),
                Bound(_text(row["bound"], "constraint bound")),
                _decimal(row["threshold"], "constraint threshold"),
            )
        )
    return OptimizationSpec(
        campaign_id,
        {"experiment_config": config.to_dict(), "input_fingerprint": config.input_fingerprint},
        _dimensions(payload),
        method,
        _int(payload.get("candidate_budget"), "candidate_budget", 1),
        _int(payload.get("seed"), "seed"),
        _text(payload.get("objective_metric"), "objective_metric"),
        direction,
        tuple(constraints),
    )


def _metrics_by_name(manifest: ExperimentManifest) -> dict[str, Metric]:
    return dict(manifest.metrics)


def _event_record(record: Record, kind: str, index: int, value: object) -> None:
    record({"kind": kind, "index": index, "value": cast(dict[str, object], _json(value))})


class ResearchCampaigns:
    """Run real, source-pinned research campaigns through a ResearchRuntime."""

    def __init__(self, runtime: ResearchRuntime) -> None:
        self.runtime = runtime

    def _base(
        self, payload: Mapping[str, object], job_id: str
    ) -> tuple[ExperimentConfig, Mapping[str, object]]:
        _keys(
            payload,
            {"backtest"},
            {
                "dimensions",
                "method",
                "candidate_budget",
                "seed",
                "objective_metric",
                "direction",
                "constraints",
            },
            "campaign",
        )
        backtest = _mapping(payload["backtest"], "backtest")
        config = self.runtime.prepare(backtest)
        if not isinstance(config, ExperimentConfig):
            raise DomainError("runtime.prepare must return ExperimentConfig")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}", job_id):
            raise DomainError("job_id contains unsupported characters or is too long")
        return config, payload

    def _run_optimization(
        self,
        payload: Mapping[str, object],
        job_id: str,
        record: Record,
        *,
        config: ExperimentConfig | None = None,
        campaign_id: str | None = None,
    ) -> tuple[OptimizationResult, OptimizationSpec, dict[int, ExperimentManifest]]:
        if config is None:
            config, _ = self._base(payload, job_id)
        cid = campaign_id or job_id
        spec = _optimization_spec(payload, config, cid)
        if any(dimension.name not in config.parameters for dimension in spec.dimensions):
            raise DomainError(
                "Optimization dimensions must name parameters of the selected strategy"
            )
        manifests: dict[int, ExperimentManifest] = {}
        _event_record(
            record,
            "optimization_specification",
            0,
            {"specification": spec.to_dict(), "fingerprint": spec.fingerprint},
        )

        def evaluate(index: int, parameters: Mapping[str, object]) -> TrialEvaluation:
            candidate_config = _config_with(config, parameters={**config.parameters, **parameters})
            run_id = f"{job_id}-opt-{cid[-20:]}-{index:05d}"
            try:
                manifest, _ = self.runtime.run(candidate_config, run_id)
            except StoreError:
                raise
            except DomainError as exc:
                raise TrialExecutionError(str(exc)) from exc
            if manifest.config != candidate_config:
                raise DomainError("Runtime returned a manifest for a different candidate config")
            manifests[index] = manifest
            return TrialEvaluation(
                _metrics_by_name(manifest),
                manifest.run_id.value,
                manifest.input_fingerprint,
                manifest.result_fingerprint,
            )

        def save(trial: OptimizationTrial) -> None:
            _event_record(record, "optimization_trial", trial.index, trial.to_dict())

        result = run_optimization(spec, evaluate, save)
        return result, spec, manifests

    def optimize(
        self, payload: Mapping[str, object], job_id: str, record: Record
    ) -> dict[str, object]:
        _keys(
            payload,
            {
                "backtest",
                "dimensions",
                "method",
                "candidate_budget",
                "seed",
                "objective_metric",
                "direction",
            },
            {"constraints"},
            "optimization",
        )
        config, _ = self._base(payload, job_id)
        result, spec, _ = self._run_optimization(payload, job_id, record, config=config)
        return cast(
            dict[str, object],
            _json(
                {
                    "kind": "optimization",
                    "job_id": job_id,
                    "specification": spec.to_dict(),
                    "specification_fingerprint": spec.fingerprint,
                    "completed": result.completed,
                    "trials": [trial.to_dict() for trial in result.trials],
                    "eligible_ranked_trial_indices": [
                        trial.index for trial in result.ranked_eligible_trials
                    ],
                    "selected_strategy": None,
                }
            ),
        )

    def walk_forward(
        self, payload: Mapping[str, object], job_id: str, record: Record
    ) -> dict[str, object]:
        data = _mapping(payload, "walk-forward")
        _keys(
            data,
            {
                "backtest",
                "dimensions",
                "method",
                "candidate_budget",
                "seed",
                "objective_metric",
                "direction",
                "training_seconds",
                "test_seconds",
                "mode",
            },
            {"constraints"},
            "walk-forward",
        )
        backtest_payload = dict(_mapping(data["backtest"], "backtest"))
        backtest_payload["boundary_liquidation"] = True
        config = self.runtime.prepare(backtest_payload)
        training_seconds = _int(data["training_seconds"], "training_seconds", 1)
        test_seconds = _int(data["test_seconds"], "test_seconds", 1)
        mode = WindowMode(_text(data["mode"], "mode"))
        pinned_config = config.to_dict()
        for mutable_field in ("start", "end", "parameters"):
            pinned_config.pop(mutable_field)
        spec = WalkForwardSpec(
            job_id,
            {"experiment_config": pinned_config},
            config.datasets,
            config.strategy_source_hash,
            config.start,
            config.end,
            timedelta(seconds=training_seconds),
            timedelta(seconds=test_seconds),
            timedelta(seconds=test_seconds),
            mode,
        )
        campaign_trials: dict[int, tuple[OptimizationResult, OptimizationSpec]] = {}

        def select(training: TrainingContext) -> FrozenSelection:
            index = training.index
            is_config = _config_with(config, start=training.start, end=training.end)
            cid = f"{job_id}-wf{index}"
            result, m9, manifests = self._run_optimization(
                data, job_id, record, config=is_config, campaign_id=cid
            )
            ranked = result.ranked_eligible_trials
            if not ranked:
                raise DomainError(
                    f"Walk-forward window {index} has no eligible in-sample candidate"
                )
            chosen = ranked[0]
            manifest = manifests.get(chosen.index)
            if manifest is None:
                raise DomainError("Selected candidate is missing its registered M8 run")
            campaign_trials[index] = (result, m9)
            return FrozenSelection(cid, m9.fingerprint, manifest)

        previous_equity: Decimal | None = None

        def execute_oos(window: WalkForwardWindow, selection: FrozenSelection) -> OOSRun:
            nonlocal previous_equity
            initial = config.initial_capital if previous_equity is None else previous_equity
            oos_config = _config_with(
                config,
                start=window.oos_start,
                end=window.oos_end,
                initial_capital=initial,
                parameters=selection.parameters,
            )
            manifest, result = self.runtime.run(oos_config, f"{job_id}-oos-{window.index:05d}")
            from quant_terminal.experiments import result_fingerprint

            previous_equity = result.nav[-1].nav
            return OOSRun(manifest, result_fingerprint(result), result)

        def save_window(row: WalkForwardWindowResult) -> None:
            _event_record(
                record,
                "walk_forward_window",
                row.window.index,
                {
                    "window": row.window,
                    "selection": row.selection.selected_manifest.to_dict(),
                    "metric_deltas": row.metric_deltas,
                    "oos_run": row.oos_run.manifest.to_dict(),
                    "oos_result_fingerprint": row.oos_run.result_fingerprint,
                },
            )

        result = run_walk_forward(spec, select, execute_oos, save_window)
        return cast(
            dict[str, object],
            _json(
                {
                    "kind": "walk_forward",
                    "job_id": job_id,
                    "specification_fingerprint": result.specification_fingerprint,
                    "completed": result.completed,
                    "windows": [
                        {
                            "window": row.window,
                            "selection": row.selection.selected_manifest.to_dict(),
                            "campaign_id": row.selection.campaign_id,
                            "campaign_fingerprint": row.selection.campaign_fingerprint,
                            "metric_deltas": row.metric_deltas,
                            "oos_run": row.oos_run.manifest.to_dict(),
                            "result_fingerprint": row.oos_run.result_fingerprint,
                            "optimization_specification": campaign_trials[row.window.index][
                                1
                            ].to_dict(),
                            "optimization_fingerprint": campaign_trials[row.window.index][
                                1
                            ].fingerprint,
                            "optimization_trials": [
                                trial.to_dict()
                                for trial in campaign_trials[row.window.index][0].trials
                            ],
                        }
                        for row in result.windows
                    ],
                    "stitched_oos_equity": result.stitched_oos_equity,
                }
            ),
        )

    def monte_carlo(
        self, payload: Mapping[str, object], job_id: str, record: Record
    ) -> dict[str, object]:
        data = _mapping(payload, "monte-carlo")
        required = {
            "source_run_ids",
            "method",
            "sample_kind",
            "path_count",
            "path_length",
            "seed",
            "currency",
            "initial_equity",
        }
        optional = {
            "periods_per_year",
            "segment_ids",
            "block_length",
            "ruin_equity_fraction",
            "annual_risk_free_rate",
            "trade_frequency_per_year",
        }
        _keys(data, required, optional, "monte-carlo")
        run_ids = data["source_run_ids"]
        if not isinstance(run_ids, (tuple, list)) or not run_ids:
            raise DomainError("source_run_ids must be a nonempty array")
        if "segment_ids" in data:
            raise DomainError("Monte Carlo segment boundaries derive from verified source runs")
        kind = SampleKind(_text(data["sample_kind"], "sample_kind"))
        manifests: list[ExperimentManifest] = []
        returns: list[Decimal] = []
        segments: list[str] = []
        sampled: list[object] = []
        sample_analytics: set[tuple[int, Decimal]] = set()
        for run_id_raw in run_ids:
            run_id = _text(run_id_raw, "source run ID")
            manifest = self.runtime.registry.get(RunId(run_id))
            report = self.runtime.read_report(run_id)
            if not isinstance(report, Mapping):
                raise DomainError("Verified report artifact must parse as an object")
            if (
                report.get("currency") != manifest.config.currency
                or _date(report.get("start"), "report.start") != manifest.config.start
                or _date(report.get("end"), "report.end") != manifest.config.end
            ):
                raise DomainError("Verified M7 report identity differs from its registered M8 run")
            analytics = _mapping(report.get("analytics_config"), "report.analytics_config")
            sample_analytics.add(
                (
                    _int(analytics.get("annualization"), "report annualization", 1),
                    _decimal(
                        analytics.get("annual_risk_free_rate"), "report annual risk-free rate"
                    ),
                )
            )
            tables = report.get("tables")
            if not isinstance(tables, (list, tuple)):
                raise DomainError("Verified report artifact has no tables")
            table_name = "session_returns" if kind is SampleKind.PERIODIC_RETURN else "trades"
            table = next(
                (
                    row
                    for row in tables
                    if isinstance(row, Mapping) and row.get("name") == table_name
                ),
                None,
            )
            if not isinstance(table, Mapping):
                raise DomainError(f"Verified report is missing {table_name}")
            columns, rows = table.get("columns"), table.get("rows")
            if not isinstance(columns, (list, tuple)) or not isinstance(rows, (list, tuple)):
                raise DomainError(f"Verified {table_name} table is malformed")
            indexes = {str(name): i for i, name in enumerate(columns)}
            if table_name == "session_returns":
                if not {"timestamp", "return"} <= indexes.keys():
                    raise DomainError("session_returns schema is not recognized")
                previous_timestamp: datetime | None = None
                for row in rows:
                    if not isinstance(row, (list, tuple)) or len(row) != len(columns):
                        raise DomainError("Malformed session return row")
                    timestamp = _date(row[indexes["timestamp"]], "session return timestamp")
                    if previous_timestamp is not None and timestamp <= previous_timestamp:
                        raise DomainError("Verified session return observations must be ordered")
                    previous_timestamp = timestamp
                    returns.append(_decimal(row[indexes["return"]], "session return"))
                    segments.append(run_id)
                    sampled.append((run_id, timestamp, row[indexes["return"]]))
            else:
                if not {"net_pnl", "entry_notional"} <= indexes.keys():
                    raise DomainError("closed trades schema is not recognized")
                for row in rows:
                    if not isinstance(row, (list, tuple)) or len(row) != len(columns):
                        raise DomainError("Malformed closed trade row")
                    notional = _decimal(row[indexes["entry_notional"]], "trade entry notional")
                    if notional <= 0:
                        raise DomainError("Closed trade entry notional must be positive")
                    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
                        trade_return = _decimal(row[indexes["net_pnl"]], "trade net PnL") / notional
                    returns.append(trade_return)
                    segments.append(run_id)
                    sampled.append(
                        (
                            run_id,
                            row[indexes.get("exit_timestamp", indexes["net_pnl"])],
                            row[indexes["net_pnl"]],
                            row[indexes["entry_notional"]],
                        )
                    )
            manifests.append(manifest)
        currency = _text(data["currency"], "currency")
        if any(manifest.config.currency != currency for manifest in manifests):
            raise DomainError("Monte Carlo currency must match every verified source run")
        if len(sample_analytics) != 1:
            raise DomainError("Monte Carlo source analytics assumptions must match across runs")
        source_annualization, source_risk_free = next(iter(sample_analytics))
        risk_free = _decimal(data.get("annual_risk_free_rate", "0"), "annual_risk_free_rate")
        if risk_free != source_risk_free:
            raise DomainError(
                "Monte Carlo risk-free assumption must match verified source analytics"
            )
        periods_per_year = (
            None
            if data.get("periods_per_year") is None
            else _int(data["periods_per_year"], "periods_per_year", 1)
        )
        if kind is SampleKind.PERIODIC_RETURN and periods_per_year != source_annualization:
            raise DomainError("Monte Carlo periods_per_year must match verified source analytics")
        if not returns:
            raise DomainError("Verified source artifacts contain no usable observations")
        method = BootstrapMethod(_text(data["method"], "method"))
        source_fingerprint = canonical_hash(
            {
                "runs": [
                    {
                        "run_id": m.run_id.value,
                        "input_fingerprint": m.input_fingerprint,
                        "result_fingerprint": m.result_fingerprint,
                    }
                    for m in manifests
                ],
                "rows": sampled,
            }
        )
        mc_spec = MonteCarloSpec(
            job_id,
            tuple(m.run_id.value for m in manifests),
            source_fingerprint,
            tuple(returns),
            method,
            kind,
            _int(data["path_count"], "path_count", 1),
            _int(data["path_length"], "path_length", 1),
            _int(data["seed"], "seed"),
            periods_per_year,
            currency,
            _decimal(data["initial_equity"], "initial_equity"),
            tuple(segments),
            None
            if data.get("block_length") is None
            else _int(data["block_length"], "block_length", 1),
            None
            if data.get("ruin_equity_fraction") is None
            else _decimal(data["ruin_equity_fraction"], "ruin_equity_fraction"),
            risk_free,
            None
            if data.get("trade_frequency_per_year") is None
            else _decimal(data["trade_frequency_per_year"], "trade_frequency_per_year"),
        )
        result = run_simulation(mc_spec)
        artifact = mc_spec.artifact_bytes(result)
        record(
            {
                "kind": "monte_carlo_result",
                "index": 0,
                "value": cast(dict[str, object], json.loads(artifact)),
            }
        )
        return cast(
            dict[str, object],
            _json(
                {
                    "kind": "monte_carlo",
                    "job_id": job_id,
                    "specification": mc_spec.to_dict(),
                    "result": result.to_dict(),
                    "result_fingerprint": result.content_fingerprint,
                }
            ),
        )

    def robustness(
        self, payload: Mapping[str, object], job_id: str, record: Record
    ) -> dict[str, object]:
        data = _mapping(payload, "robustness")
        _keys(data, {"baseline_run_id", "scenarios"}, set(), "robustness")
        baseline_id = _text(data["baseline_run_id"], "baseline_run_id")
        baseline = self.runtime.registry.get(RunId(baseline_id))
        raw_scenarios = data["scenarios"]
        if not isinstance(raw_scenarios, (tuple, list)) or not raw_scenarios:
            raise DomainError("scenarios must be a nonempty array")
        scenarios: list[RobustnessScenario] = []
        for index, raw in enumerate(raw_scenarios):
            row = _mapping(raw, f"scenarios[{index}]")
            _keys(
                row, {"scenario_id", "kind", "changes", "rationale"}, set(), f"scenarios[{index}]"
            )
            changes = _mapping(row["changes"], "scenario changes")
            scenarios.append(
                RobustnessScenario(
                    _text(row["scenario_id"], "scenario_id"),
                    ScenarioKind(_text(row["kind"], "scenario kind")),
                    tuple((key, value) for key, value in changes.items()),
                    _text(row["rationale"], "scenario rationale"),
                )
            )
        spec = RobustnessSpec(job_id, baseline, tuple(scenarios))
        evaluator = ResearchScenarioAdapter(
            self.runtime.registry,
            self.runtime.data,
            baseline,
            self.runtime.strategy,
            run_namespace=job_id,
            project_root=self.runtime.project_root,
            strategy_source=self.runtime.strategy_source,
        )

        def save(raw: bytes) -> None:
            _event_record(record, "robustness_scenario", len(events), json.loads(raw))
            events.append(raw)

        events: list[bytes] = []
        outcome = run_robustness(spec, self.runtime.registry, evaluator, save)
        return cast(
            dict[str, object],
            _json(
                {
                    "kind": "robustness",
                    "job_id": job_id,
                    "specification_fingerprint": outcome.specification_fingerprint,
                    "completed": outcome.completed,
                    "scenarios": [json.loads(row) for row in outcome.rows],
                }
            ),
        )
