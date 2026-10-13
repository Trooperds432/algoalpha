"""Strict headless backtest requests over trusted built-in research strategies."""

from __future__ import annotations

import hashlib
import io
import json
import platform
import re
import stat
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import cast

from quant_terminal import __version__
from quant_terminal.analytics import AnalyticsConfig, analyze
from quant_terminal.backtesting.engine import BacktestEngine
from quant_terminal.backtesting.models import BacktestConfig, BacktestResult
from quant_terminal.backtesting.strategies import (
    BuyAndHold,
    MovingAverageCross,
    SimpleMomentum,
    Strategy,
)
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId, RunId
from quant_terminal.data.storage import DatasetStore
from quant_terminal.execution.models import (
    ConfiguredSlippage,
    ExecutionConfig,
    PerUnitCommission,
    SlippageKind,
)
from quant_terminal.experiments import (
    DatasetIdentity,
    ExperimentConfig,
    ExperimentManifest,
    ExperimentStore,
    IntegrityError,
    ReplayOutcome,
    build_source_bundle,
    result_fingerprint,
)
from quant_terminal.reporting import build_report, export_bundle
from quant_terminal.robustness.adapter import analytics_config
from quant_terminal.robustness.engine import execution_config

_PARAMETERS = {
    "buy_and_hold": {"instrument_id", "quantity"},
    "moving_average_cross": {"instrument_id", "quantity", "fast_period", "slow_period"},
    "simple_momentum": {"instrument_id", "quantity", "lookback"},
}


def _object(value: object, allowed: set[str], required: set[str]) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise DomainError("Expected a JSON object with string keys")
    if set(value) - allowed or required - set(value):
        raise DomainError(f"Unknown or missing request fields: allowed={sorted(allowed)}")
    return dict(value)


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainError("Expected nonempty text")
    return value


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise DomainError("Financial values must be exact decimal strings")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise DomainError("Invalid decimal string") from exc
    if not result.is_finite():
        raise DomainError("Financial values must be finite")
    return result


def _integer(value: object) -> int:
    if type(value) is not int:
        raise DomainError("Expected a JSON integer")
    return value


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise DomainError("Expected a JSON boolean")
    return value


def _stamp(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(_text(value))
    except ValueError as exc:
        raise DomainError("Invalid ISO timestamp") from exc
    if result.utcoffset() is None:
        raise DomainError("Timestamps must include a timezone")
    return result.astimezone(UTC)


class ResearchRuntime:
    def __init__(self, root: Path, project_root: Path) -> None:
        self.root = Path(root).resolve()
        self.project_root = Path(project_root).resolve(strict=True)
        if self.root.is_relative_to(self.project_root):
            raise DomainError("Runtime state must be outside the project source tree")
        if self.project_root != Path(__file__).resolve().parents[3]:
            raise DomainError("Project root must identify the executing source tree")
        self.strategy_source = self.project_root / "src/quant_terminal/backtesting/strategies.py"
        self.lock_path = self.project_root / "requirements-dev.lock"
        self.data = DatasetStore(self.root / "data")
        self.registry = ExperimentStore(self.root / "runs")

    def strategies(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {"name": name, "parameters": sorted(fields)} for name, fields in _PARAMETERS.items()
        )

    def snapshots(self) -> tuple[dict[str, object], ...]:
        snapshots = []
        for path in sorted(self.data.root.iterdir()):
            if not re.fullmatch(r"[0-9a-f]{64}", path.name):
                continue
            snapshot = self.data.load_snapshot(path.name)
            snapshots.append(
                {
                    "snapshot_id": snapshot.snapshot_id,
                    "instrument_id": snapshot.instrument.instrument_id.value,
                    "currency": snapshot.instrument.currency,
                    "row_count": snapshot.row_count,
                    "source": snapshot.source,
                    "content_hash": snapshot.content_hash,
                }
            )
        return tuple(snapshots)

    def prepare(self, payload: Mapping[str, object]) -> ExperimentConfig:
        required = {
            "strategy",
            "parameters",
            "snapshot_ids",
            "start",
            "end",
            "currency",
            "initial_capital",
            "execution",
        }
        data = _object(
            payload,
            required
            | {
                "seed",
                "analytics",
                "benchmark",
                "max_order_notional",
                "allow_short",
                "short_assumption",
                "allow_zero_costs",
                "boundary_liquidation",
            },
            required,
        )
        name = _text(data["strategy"])
        if name not in _PARAMETERS:
            raise DomainError("Unsupported built-in strategy")
        params = _object(data["parameters"], _PARAMETERS[name], _PARAMETERS[name])
        _text(params["instrument_id"])
        _decimal(params["quantity"])
        for key in set(params) - {"instrument_id", "quantity"}:
            _integer(params[key])
        snapshot_ids = data["snapshot_ids"]
        if (
            not isinstance(snapshot_ids, (list, tuple))
            or not snapshot_ids
            or len(snapshot_ids) > 100
        ):
            raise DomainError("snapshot_ids must contain between 1 and 100 snapshot identities")
        snapshots = sorted(
            (self.data.load_snapshot(_text(item)) for item in snapshot_ids),
            key=lambda s: s.instrument.instrument_id.value,
        )
        if len({s.instrument.instrument_id for s in snapshots}) != len(snapshots):
            raise DomainError("Only one snapshot per instrument is permitted")
        execution_data = _object(
            data["execution"],
            {
                "commission_per_unit",
                "commission_minimum",
                "slippage_kind",
                "slippage_value",
                "maximum_participation",
                "zero_cost_assumption",
            },
            {"commission_per_unit"},
        )
        minimum = execution_data.get("commission_minimum")
        try:
            slip_kind = SlippageKind(_text(execution_data.get("slippage_kind", "none")))
        except ValueError as exc:
            raise DomainError("Unsupported slippage kind") from exc
        execution = ExecutionConfig(
            maximum_participation=_decimal(execution_data.get("maximum_participation", "1")),
            commission=PerUnitCommission(
                _decimal(execution_data["commission_per_unit"]),
                None if minimum is None else _decimal(minimum),
            ),
            slippage=ConfiguredSlippage(
                slip_kind, _decimal(execution_data.get("slippage_value", "0"))
            ),
            zero_cost_assumption=cast(str | None, execution_data.get("zero_cost_assumption")),
        )
        assert isinstance(execution.commission, PerUnitCommission)
        assert isinstance(execution.slippage, ConfiguredSlippage)
        analytic_data = _object(
            data.get("analytics", {}),
            {"annualization", "annual_risk_free_rate", "annual_mar", "var_confidences"},
            set(),
        )
        confidences = analytic_data.get("var_confidences", ["0.95", "0.99", "0.999"])
        if not isinstance(confidences, (tuple, list)):
            raise DomainError("var_confidences must be a JSON array")
        analytics = AnalyticsConfig(
            _integer(analytic_data.get("annualization", 252)),
            _decimal(analytic_data.get("annual_risk_free_rate", "0")),
            _decimal(analytic_data.get("annual_mar", "0")),
            tuple(_decimal(value) for value in confidences),
        )
        backtest = BacktestConfig(
            _decimal(data["initial_capital"]),
            _text(data["currency"]),
            _decimal(data.get("max_order_notional", "1000000")),
            _boolean(data.get("allow_short", False)),
            cast(str | None, data.get("short_assumption")),
            _boolean(data.get("allow_zero_costs", False)),
        )
        benchmark = data.get("benchmark")
        if benchmark is not None:
            _text(benchmark)
            if benchmark not in {s.instrument.instrument_id.value for s in snapshots}:
                raise DomainError("Benchmark has no pinned snapshot")
        if any(s.instrument.currency != backtest.currency for s in snapshots):
            raise DomainError("Dataset currencies must match backtest currency")
        config = ExperimentConfig(
            strategy_id=f"builtin.{name}.v1",
            strategy_name=name,
            strategy_source_hash=hashlib.sha256(self.strategy_source.read_bytes()).hexdigest(),
            project_source_hash=hashlib.sha256(build_source_bundle(self.project_root)).hexdigest(),
            git_commit=None,
            engine_version=__version__,
            dependency_lock_hash=hashlib.sha256(self.lock_path.read_bytes()).hexdigest(),
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
            start=_stamp(data["start"]),
            end=_stamp(data["end"]),
            currency=backtest.currency,
            initial_capital=backtest.initial_cash,
            parameters=params,
            seed=_integer(data.get("seed", 0)),
            backtest_settings={
                "max_order_notional": backtest.max_order_notional,
                "allow_short": backtest.allow_short,
                "short_assumption": backtest.short_assumption,
                "allow_zero_costs": backtest.allow_zero_costs,
                "boundary_liquidation": _boolean(data.get("boundary_liquidation", False)),
            },
            analytics_settings=asdict(analytics),
            benchmark=None if benchmark is None else {"instrument_id": benchmark},
            execution_assumptions={
                "maximum_participation": execution.maximum_participation,
                "commission": {
                    "kind": "per_unit",
                    "per_unit_rate": execution.commission.per_unit_rate,
                    "minimum": execution.commission.minimum,
                },
                "commission_model_repr": repr(execution.commission),
                "slippage": {"kind": slip_kind.value, "value": execution.slippage.value},
                "slippage_model_repr": repr(execution.slippage),
                "zero_cost_assumption": execution.zero_cost_assumption,
            },
        )
        BacktestEngine(self.data, snapshots, self.strategy(config), backtest, execution)
        return config

    def strategy(self, config: ExperimentConfig) -> Strategy:
        name = config.strategy_name
        if name not in _PARAMETERS or config.strategy_id != f"builtin.{name}.v1":
            raise DomainError("Unknown built-in strategy identity")
        params = _object(config.parameters, _PARAMETERS[name], _PARAMETERS[name])
        instrument_id = InstrumentId(_text(params["instrument_id"]))
        instrument = next(
            (
                self.data.load_snapshot(item.snapshot_id).instrument
                for item in config.datasets
                if instrument_id.value in item.instrument_ids
            ),
            None,
        )
        if instrument is None:
            raise DomainError("Strategy instrument has no pinned snapshot")
        quantity = _decimal(params["quantity"])
        if name == "buy_and_hold":
            return BuyAndHold(instrument_id, quantity)
        if name == "moving_average_cross":
            return MovingAverageCross(
                instrument,
                quantity,
                _integer(params["fast_period"]),
                _integer(params["slow_period"]),
            )
        return SimpleMomentum(instrument, quantity, _integer(params["lookback"]))

    def _source_artifacts(self, config: ExperimentConfig) -> dict[str, bytes]:
        source = self.strategy_source.read_bytes()
        bundle = build_source_bundle(self.project_root)
        if (
            hashlib.sha256(source).hexdigest() != config.strategy_source_hash
            or hashlib.sha256(bundle).hexdigest() != config.project_source_hash
            or hashlib.sha256(self.lock_path.read_bytes()).hexdigest()
            != config.dependency_lock_hash
        ):
            raise IntegrityError(
                "Executing source or dependency lock differs from prepared configuration"
            )
        return {"strategy-source.py": source, "source-bundle.zip": bundle}

    def run(
        self, config: ExperimentConfig, run_id: str
    ) -> tuple[ExperimentManifest, BacktestResult]:
        identity = RunId(run_id)
        artifacts = self._source_artifacts(config)
        snapshots = tuple(self.data.load_snapshot(item.snapshot_id) for item in config.datasets)
        for expected, actual in zip(config.datasets, snapshots, strict=True):
            if expected != DatasetIdentity(
                actual.snapshot_id,
                actual.content_hash,
                actual.input_hash,
                actual.row_count,
                (actual.instrument.instrument_id.value,),
            ):
                raise IntegrityError("Dataset identity differs from prepared configuration")
        settings = config.backtest_settings
        backtest = BacktestConfig(
            config.initial_capital,
            config.currency,
            _decimal(settings["max_order_notional"]),
            _boolean(settings["allow_short"]),
            cast(str | None, settings["short_assumption"]),
            _boolean(settings["allow_zero_costs"]),
        )
        benchmark = (
            None
            if config.benchmark is None
            else InstrumentId(_text(config.benchmark["instrument_id"]))
        )
        result = BacktestEngine(
            self.data, snapshots, self.strategy(config), backtest, execution_config(config)
        ).run(
            start=config.start,
            end=config.end,
            benchmark=benchmark,
            boundary_liquidation=_boolean(settings.get("boundary_liquidation", False)),
        )
        self._source_artifacts(config)
        analytics = analytics_config(config)
        manifest = ExperimentManifest(
            identity,
            datetime.now(UTC),
            config,
            result_fingerprint(result),
            analyze(result, analytics).metrics,
            platform.python_version(),
            {"platform": platform.system(), "adapter": "application-runtime-v1"},
        )
        with tempfile.TemporaryDirectory(prefix="quant-runtime-report-") as temporary:
            report = export_bundle(build_report(result, analytics), Path(temporary) / "report")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
                for path in sorted(report.rglob("*")):
                    if path.is_file():
                        entry = zipfile.ZipInfo(
                            path.relative_to(report).as_posix(), (1980, 1, 1, 0, 0, 0)
                        )
                        entry.create_system = 3
                        entry.external_attr = (stat.S_IFREG | 0o644) << 16
                        archive.writestr(entry, path.read_bytes())
        artifacts["report-bundle.zip"] = buffer.getvalue()
        stored = self.registry.create(
            manifest,
            ReplayOutcome(
                result,
                config.strategy_source_hash,
                config.project_source_hash,
                config.datasets,
                manifest.input_fingerprint,
            ),
            artifacts,
        )
        return stored, result

    def read_report(self, run_id: str) -> dict[str, object]:
        payload = self.registry.read_artifact(RunId(run_id), "report-bundle.zip")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            report = json.loads(archive.read("report.json"))
        if not isinstance(report, dict):
            raise IntegrityError("Stored report must be a JSON object")
        return cast(dict[str, object], report)
