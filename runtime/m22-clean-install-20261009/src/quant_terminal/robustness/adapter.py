"""Trusted M5/M8 scenario reruns with delayed observations and fixed source data."""

from __future__ import annotations

import hashlib
import inspect
import io
import platform
import stat
import tempfile
import zipfile
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import cast

from quant_terminal.analytics import AnalyticsConfig, analyze
from quant_terminal.backtesting.engine import BacktestEngine
from quant_terminal.backtesting.models import BacktestConfig
from quant_terminal.backtesting.strategies import HistoryFeed, Strategy, StrategyContext
from quant_terminal.core.clock import utc
from quant_terminal.core.exceptions import DomainError, FutureDataError
from quant_terminal.core.identifiers import InstrumentId, RunId
from quant_terminal.data.feed import DataFeed
from quant_terminal.data.model import Bar
from quant_terminal.data.storage import DatasetStore
from quant_terminal.experiments import (
    ExperimentConfig,
    ExperimentManifest,
    ExperimentStore,
    IntegrityError,
    ReplayOutcome,
    StoreError,
    build_source_bundle,
    canonical_hash,
    canonical_json,
    result_fingerprint,
)
from quant_terminal.orders.models import OrderIntent
from quant_terminal.reporting import build_report, export_bundle

from .engine import ScenarioExecutionError, execution_config
from .models import RobustnessScenario


class _DelayedFeed(DataFeed):
    def __init__(self, source: HistoryFeed, cutoff: datetime, now: datetime) -> None:
        self.source, self.cutoff, self.now = source, cutoff, now

    def _end(self, end: datetime) -> datetime:
        if utc(end) > self.now:
            raise FutureDataError("Requested delayed history is in the future")
        return min(utc(end), self.cutoff)

    def history(self, *, end: datetime, count: int) -> tuple[Bar, ...]:
        return self.source.history(end=self._end(end), count=count)

    def between(self, *, start: datetime, end: datetime) -> tuple[Bar, ...]:
        bound = self._end(end)
        if utc(start) >= bound:
            return ()
        return self.source.between(start=start, end=bound)


class ObservationOverlay:
    """Delay strategy observations; execution and marking still use original bars."""

    def __init__(self, strategy: Strategy, delay: int, universe: tuple[str, ...]) -> None:
        if type(delay) is not int or delay < 0:
            raise DomainError("Observation delay must be a nonnegative bar count")
        if delay and len(universe) != 1:
            raise DomainError("Delayed observations currently require one instrument")
        self.strategy, self.delay, self.universe = strategy, delay, universe
        self.name = strategy.name
        self.pending: deque[Bar] = deque()
        self.observations: list[dict[str, object]] = []

    def on_bar(self, context: StrategyContext, bar: Bar) -> tuple[OrderIntent, ...]:
        if bar.instrument_id.value not in self.universe:
            return ()
        self.pending.append(bar)
        if len(self.pending) <= self.delay:
            return ()
        original = self.pending.popleft()
        feeds = {
            instrument: _DelayedFeed(feed, original.available_at, context.current_time)
            for instrument, feed in context._feeds.items()
            if instrument.value in self.universe
        }
        instruments = {
            instrument: meta
            for instrument, meta in context._instruments.items()
            if instrument.value in self.universe
        }
        limited = replace(
            context, _feeds=MappingProxyType(feeds), _instruments=MappingProxyType(instruments)
        )
        self.observations.append(
            {
                "instrument_id": original.instrument_id.value,
                "bar_start": original.start,
                "original_available_at": original.available_at,
                "observed_at": context.current_time,
            }
        )
        try:
            proposals = tuple(self.strategy.on_bar(limited, original))
        except StoreError:
            raise
        except Exception as exc:
            raise ScenarioExecutionError(
                f"Strategy callback failed: {type(exc).__name__}: {exc}"
            ) from exc
        if any(intent.instrument.instrument_id.value not in self.universe for intent in proposals):
            raise DomainError("Strategy emitted an order outside the perturbed universe")
        return proposals


def analytics_config(config: ExperimentConfig) -> AnalyticsConfig:
    settings = config.analytics_settings
    if settings.get("risk_of_ruin") is not None:
        raise DomainError("Scenario adapter requires an explicit supported analytics configuration")
    return AnalyticsConfig(
        annualization=int(str(settings["annualization"])),
        annual_risk_free_rate=Decimal(str(settings.get("annual_risk_free_rate", "0"))),
        annual_mar=Decimal(str(settings.get("annual_mar", "0"))),
        var_confidences=tuple(
            Decimal(str(x))
            for x in cast(
                tuple[object, ...], settings.get("var_confidences", ("0.95", "0.99", "0.999"))
            )
        ),
    )


class ResearchScenarioAdapter:
    """Run trusted strategy factories and register fresh artifacts in M8."""

    def __init__(
        self,
        registry: ExperimentStore,
        data: DatasetStore,
        baseline: ExperimentManifest,
        strategy_factory: Callable[[ExperimentConfig], Strategy],
        *,
        run_namespace: str,
        project_root: Path,
        strategy_source: Path,
    ) -> None:
        self.registry, self.data, self.baseline = registry, data, baseline
        self.strategy_factory, self.run_namespace = strategy_factory, run_namespace
        self.project_root, self.strategy_source = project_root.resolve(), strategy_source.resolve()

    def _verify_source(self, config: ExperimentConfig) -> None:
        if (
            hashlib.sha256(self.strategy_source.read_bytes()).hexdigest()
            != config.strategy_source_hash
        ):
            raise IntegrityError("Executing strategy source changed since the baseline")
        if (
            hashlib.sha256(build_source_bundle(self.project_root)).hexdigest()
            != config.project_source_hash
        ):
            raise IntegrityError("Executing project source changed since the baseline")

    def __call__(
        self, scenario: RobustnessScenario, config: ExperimentConfig
    ) -> ExperimentManifest:
        self._verify_source(config)
        snapshots = tuple(self.data.load_snapshot(item.snapshot_id) for item in config.datasets)
        for expected, actual in zip(config.datasets, snapshots, strict=True):
            if (
                expected.content_hash,
                expected.input_hash,
                expected.row_count,
                expected.instrument_ids,
            ) != (
                actual.content_hash,
                actual.input_hash,
                actual.row_count,
                (actual.instrument.instrument_id.value,),
            ):
                raise IntegrityError("Scenario dataset no longer matches pinned identity")
        raw_overlay = config.backtest_settings.get("robustness_overlay")
        if not isinstance(raw_overlay, Mapping):
            raise DomainError("Scenario configuration requires a pinned observation overlay")
        universe = tuple(cast(tuple[str, ...], raw_overlay["universe"]))
        delay = raw_overlay["bar_delay"]
        if type(delay) is not int:
            raise DomainError("Invalid observation delay")
        execution_bars = tuple(
            bar
            for snapshot in snapshots
            if snapshot.instrument.instrument_id.value in universe
            for bar in self.data.query_bars(snapshot, start=config.start, end=config.end)
            if bar.available_at <= config.end
        )
        if delay:
            if len(execution_bars) <= delay:
                raise ScenarioExecutionError("Delay leaves no observable bars in the scenario")
            ordered = sorted(execution_bars, key=lambda bar: bar.start)
            if any(
                left.available_at >= right.available_at
                for left, right in zip(ordered, ordered[1:], strict=False)
            ):
                raise DomainError("Ambiguous delayed observation timestamp mapping")
        try:
            underlying = self.strategy_factory(config)
        except StoreError:
            raise
        except Exception as exc:
            raise ScenarioExecutionError(
                f"Strategy factory failed: {type(exc).__name__}: {exc}"
            ) from exc
        if Path(inspect.getfile(type(underlying))).resolve() != self.strategy_source:
            raise IntegrityError("Strategy factory returned code outside the verified source")
        strategy = ObservationOverlay(underlying, delay, universe)
        settings = config.backtest_settings
        backtest = BacktestConfig(
            config.initial_capital,
            config.currency,
            Decimal(str(settings["max_order_notional"])),
            cast(bool, settings["allow_short"]),
            cast(str | None, settings["short_assumption"]),
            cast(bool, settings["allow_zero_costs"]),
        )
        benchmark = (
            None
            if config.benchmark is None
            else InstrumentId(str(config.benchmark["instrument_id"]))
        )
        result = BacktestEngine(
            self.data, snapshots, strategy, backtest, execution_config(config)
        ).run(
            start=config.start,
            end=config.end,
            benchmark=benchmark,
            boundary_liquidation=cast(bool, settings.get("boundary_liquidation", False)),
        )
        if delay and any(
            order.remaining_quantity > 0
            and order.status.value in {"accepted", "partially_filled"}
            and not any(
                bar.instrument_id == order.intent.instrument.instrument_id
                and order.accepted_at is not None
                and bar.start >= order.accepted_at
                and bar.volume > 0
                for bar in execution_bars
            )
            for order in result.orders
        ):
            raise ScenarioExecutionError("Delayed order has no executable bar in the scenario")
        self._verify_source(config)
        result = replace(
            result,
            metadata=tuple(
                sorted(
                    (
                        *result.metadata,
                        ("robustness_overlay", canonical_json(raw_overlay).decode("utf-8")),
                    )
                )
            ),
        )
        analytics = analytics_config(config)
        identity = canonical_hash({"namespace": self.run_namespace, "config": config.to_dict()})
        manifest = replace(
            self.baseline,
            run_id=RunId("robust-" + identity[:40]),
            created_at=datetime.now(UTC),
            config=config,
            result_fingerprint=result_fingerprint(result),
            metrics=analyze(result, analytics).metrics,
            artifacts=(),
            tags=("robustness", scenario.kind.value),
            python_version=platform.python_version(),
            runtime_metadata={"platform": platform.system(), "adapter": "robustness-v1"},
        )
        with tempfile.TemporaryDirectory(prefix="quant-robustness-") as temporary:
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
        artifacts = {
            name: self.registry.read_artifact(self.baseline.run_id, name)
            for name in ("source-bundle.zip", "strategy-source.py")
        }
        artifacts.update(
            {
                "report-bundle.zip": buffer.getvalue(),
                "observation-overlay.json": canonical_json(
                    {
                        "configuration": raw_overlay,
                        "observations": strategy.observations,
                        "undelivered_tail": len(strategy.pending),
                    }
                ),
            }
        )
        outcome = ReplayOutcome(
            result,
            config.strategy_source_hash,
            config.project_source_hash,
            config.datasets,
            manifest.input_fingerprint,
        )
        return self.registry.create(manifest, outcome, artifacts)
