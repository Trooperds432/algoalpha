"""Leakage-aware walk-forward orchestration and OOS-only equity stitching."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from quant_terminal.backtesting.models import BacktestResult
from quant_terminal.core.enums import OrderStatus
from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments import ExperimentManifest, result_fingerprint
from quant_terminal.experiments.models import canonical_json

from .models import (
    FrozenSelection,
    MetricDelta,
    OOSRun,
    StitchedEquityPoint,
    TrainingContext,
    WalkForwardSpec,
    WalkForwardWindow,
    WalkForwardWindowResult,
    WindowMode,
)


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    walk_forward_id: str
    specification_fingerprint: str
    windows: tuple[WalkForwardWindowResult, ...]
    stitched_oos_equity: tuple[StitchedEquityPoint, ...]
    completed: bool


@dataclass(frozen=True, slots=True)
class ParameterStability:
    parameter: str
    values_by_window: tuple[object, ...]
    changes_between_windows: int
    distinct_values: int


Optimizer = Callable[[TrainingContext], FrozenSelection]
OOSExecutor = Callable[[WalkForwardWindow, FrozenSelection], OOSRun]
WindowRecorder = Callable[[WalkForwardWindowResult], None]


def plan_windows(spec: WalkForwardSpec) -> tuple[WalkForwardWindow, ...]:
    """Create non-overlapping OOS periods with rolling or expanding IS bounds."""
    cursor = spec.start + spec.training_duration
    windows: list[WalkForwardWindow] = []
    while cursor < spec.end:
        oos_end = min(cursor + spec.test_duration, spec.end)
        train_start = (
            spec.start
            if spec.mode is WindowMode.EXPANDING
            else max(spec.start, cursor - spec.training_duration)
        )
        windows.append(WalkForwardWindow(len(windows), train_start, cursor, cursor, oos_end))
        cursor += spec.step
    if not windows:
        raise DomainError("Walk-forward spec produced no complete windows")
    return tuple(windows)


def _pinned_initial_capital(spec: WalkForwardSpec) -> Decimal:
    config = spec.identity.get("experiment_config")
    if not isinstance(config, Mapping) or "initial_capital" not in config:
        raise DomainError("Walk-forward identity is missing pinned experiment capital")
    try:
        capital = Decimal(str(config["initial_capital"]))
    except Exception as exc:
        raise DomainError("Pinned experiment capital is invalid") from exc
    if not capital.is_finite() or capital <= 0:
        raise DomainError("Pinned experiment capital must be positive and finite")
    return capital


def _check_manifest_identity(
    manifest: ExperimentManifest,
    spec: WalkForwardSpec,
    *,
    start: datetime,
    end: datetime,
) -> None:
    config = manifest.config
    if config.start != start or config.end != end:
        raise DomainError("Stored experiment interval does not match its walk-forward window")
    if config.datasets != spec.datasets:
        raise DomainError("Stored experiment data identities differ from the walk-forward spec")
    if config.strategy_source_hash != spec.strategy_source_hash:
        raise DomainError("Stored strategy source differs from the walk-forward spec")
    expected = spec.identity.get("experiment_config")
    if not isinstance(expected, Mapping):
        raise DomainError("Walk-forward identity must pin the fixed experiment configuration")
    fixed_fields = {
        "strategy_id",
        "strategy_name",
        "strategy_source_hash",
        "project_source_hash",
        "git_commit",
        "engine_version",
        "dependency_lock_hash",
        "datasets",
        "currency",
        "seed",
        "backtest_settings",
        "analytics_settings",
        "benchmark",
        "execution_assumptions",
    }
    if set(expected) != fixed_fields | {"initial_capital"}:
        raise DomainError("Pinned experiment_config must contain every fixed M8 input field")
    actual = config.to_dict()
    for name in fixed_fields:
        if canonical_json(expected[name]) != canonical_json(actual[name]):
            raise DomainError(f"M8 experiment input {name} differs from the walk-forward spec")


def _verify_pair(
    result: WalkForwardWindowResult,
    spec: WalkForwardSpec,
    expected: WalkForwardWindow,
) -> None:
    if result.window != expected:
        raise DomainError("Resume window does not match the original walk-forward plan")
    selected = result.selection.selected_manifest
    _check_manifest_identity(selected, spec, start=expected.train_start, end=expected.train_end)
    oos = result.oos_run.manifest
    _check_manifest_identity(oos, spec, start=expected.oos_start, end=expected.oos_end)
    _check_selected_strategy(selected, oos)
    if selected.config.initial_capital != _pinned_initial_capital(spec):
        raise DomainError("IS selection capital differs from the pinned M8 experiment")
    if not result.oos_run.result.nav or (
        result.oos_run.result.nav[0].nav != oos.config.initial_capital
    ):
        raise DomainError("OOS result initial NAV differs from its funded initial capital")
    if result_fingerprint(result.oos_run.result) != result.oos_run.result_fingerprint:
        raise DomainError("Stored OOS run fingerprint differs from its M5 result")
    if oos.result_fingerprint != result.oos_run.result_fingerprint:
        raise DomainError("OOS M8 manifest fingerprint differs from the supplied result")
    _check_result_times(result.oos_run.result, expected)
    if result.metric_deltas != _deltas(result.selection.metrics, result.oos_run.metrics):
        raise DomainError("Stored IS/OOS metric degradation does not match the source manifests")


def _check_selected_strategy(selected: ExperimentManifest, oos: ExperimentManifest) -> None:
    left, right = selected.config, oos.config
    if (
        right.strategy_id != left.strategy_id
        or right.strategy_name != left.strategy_name
        or right.strategy_source_hash != left.strategy_source_hash
        or dict(right.parameters) != dict(left.parameters)
    ):
        raise DomainError("OOS strategy identity or frozen parameters changed after selection")
    fixed_inputs = (
        "project_source_hash",
        "git_commit",
        "engine_version",
        "dependency_lock_hash",
        "currency",
        "seed",
        "backtest_settings",
        "analytics_settings",
        "benchmark",
        "execution_assumptions",
    )
    if any(getattr(left, name) != getattr(right, name) for name in fixed_inputs):
        raise DomainError("OOS execution or application inputs changed after IS selection")


def _check_result_times(result: BacktestResult, window: WalkForwardWindow) -> None:
    if result.start != window.oos_start or result.end != window.oos_end:
        raise DomainError("OOS BacktestResult interval does not match the test window")
    nav_times = [point.timestamp for point in result.nav]
    fill_times = [fill.timestamp for fill in result.fills]
    observed_times = [fill.observed_at for fill in result.fills]
    benchmark_times = [point.timestamp for point in result.benchmark]
    event_times = [event.timestamp for order in result.orders for event in order.events]
    order_times = [order.intent.created_at for order in result.orders]
    order_times.extend(
        order.accepted_at for order in result.orders if order.accepted_at is not None
    )
    timestamps = (
        nav_times + fill_times + observed_times + benchmark_times + event_times + order_times
    )
    if not nav_times or nav_times[0] != window.oos_start:
        raise DomainError("OOS result must include its initial equity checkpoint")
    if any(not window.oos_start <= timestamp < window.oos_end for timestamp in timestamps):
        raise DomainError("OOS result contains an event outside its half-open test interval")
    if (
        nav_times != sorted(nav_times)
        or observed_times != sorted(observed_times)
        or benchmark_times != sorted(benchmark_times)
        or any(
            [event.timestamp for event in order.events]
            != sorted(event.timestamp for event in order.events)
            for order in result.orders
        )
    ):
        raise DomainError("OOS observation, NAV, benchmark, or per-order event times are unordered")


def _deltas(
    in_sample: Mapping[str, object], out_of_sample: Mapping[str, object]
) -> tuple[MetricDelta, ...]:
    from quant_terminal.analytics.metrics import Metric

    deltas: list[MetricDelta] = []
    for name in sorted(set(in_sample) | set(out_of_sample)):
        is_metric = in_sample.get(name)
        oos_metric = out_of_sample.get(name)
        if is_metric is not None and not isinstance(is_metric, Metric):
            raise DomainError("IS metrics must use typed M6 Metric values")
        if oos_metric is not None and not isinstance(oos_metric, Metric):
            raise DomainError("OOS metrics must use typed M6 Metric values")
        is_value = None if is_metric is None else is_metric.value
        oos_value = None if oos_metric is None else oos_metric.value
        if is_value is None or oos_value is None:
            delta = None
        else:
            with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
                delta = oos_value - is_value
        reason = None
        if delta is None:
            reason = (
                (is_metric.reason if isinstance(is_metric, Metric) and is_value is None else None)
                or (
                    oos_metric.reason
                    if isinstance(oos_metric, Metric) and oos_value is None
                    else None
                )
                or "metric unavailable in IS or OOS"
            )
        deltas.append(MetricDelta(name, is_value, oos_value, delta, reason))
    return tuple(deltas)


def stitch_oos_equity(
    rows: tuple[WalkForwardWindowResult, ...],
) -> tuple[StitchedEquityPoint, ...]:
    """Scale only OOS NAV checkpoints into a continuous reinvested equity path."""
    output: list[StitchedEquityPoint] = []
    previous_terminal: Decimal | None = None
    for row in rows:
        result = row.oos_run.result
        if not result.nav or result.nav[0].timestamp != row.window.oos_start:
            raise DomainError("OOS run is missing its start NAV checkpoint")
        if previous_terminal is not None and result.nav[0].nav != previous_terminal:
            raise DomainError("OOS window was not funded from prior terminal equity")
        if any(position.quantity != 0 for position in result.nav[-1].positions):
            raise DomainError("OOS boundary must have no residual positions after liquidation")
        if any(
            order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
            for order in result.orders
        ):
            raise DomainError("OOS boundary cannot retain active orders")
        run_id = row.oos_run.manifest.run_id.value
        for point in result.nav:
            timestamp = point.timestamp
            if not row.window.oos_start <= timestamp < row.window.oos_end:
                raise DomainError("Stitched path cannot contain in-sample or boundary data")
            output.append(StitchedEquityPoint(timestamp, point.nav, row.window.index, run_id))
        previous_terminal = result.nav[-1].nav
    return tuple(output)


def parameter_stability(
    rows: tuple[WalkForwardWindowResult, ...],
) -> tuple[ParameterStability, ...]:
    """Summarize frozen parameter changes across OOS windows."""
    if not rows:
        return ()
    names = tuple(sorted(rows[0].selection.parameters))
    if any(tuple(sorted(row.selection.parameters)) != names for row in rows):
        raise DomainError("Selected parameter dimensions changed between windows")
    summaries = []
    for name in names:
        values = tuple(row.selection.parameters[name] for row in rows)
        changes = sum(left != right for left, right in zip(values, values[1:], strict=False))
        distinct = len({canonical_json(value) for value in values})
        summaries.append(ParameterStability(name, values, changes, distinct))
    return tuple(summaries)


def run_walk_forward(
    spec: WalkForwardSpec,
    optimizer: Optimizer,
    execute_oos: OOSExecutor,
    recorder: WindowRecorder,
    *,
    start_index: int = 0,
    existing: tuple[WalkForwardWindowResult, ...] = (),
    cancelled: Callable[[], bool] | None = None,
) -> WalkForwardResult:
    """Select on an IS-only capability, freeze it, then evaluate one OOS interval."""
    if not all(callable(callback) for callback in (optimizer, execute_oos, recorder)):
        raise DomainError("Optimizer, OOS executor, and durable window recorder are required")
    windows = plan_windows(spec)
    if type(start_index) is not int or not 0 <= start_index <= len(windows):
        raise DomainError("Invalid walk-forward resume cursor")
    rows = list(existing)
    if len(rows) != start_index:
        raise DomainError("Resume requires the contiguous completed-window prefix")
    for index, row in enumerate(rows):
        _verify_pair(row, spec, windows[index])
        if index > 0:
            prior_terminal = rows[index - 1].oos_run.result.nav[-1].nav
            if row.oos_run.manifest.config.initial_capital != prior_terminal:
                raise DomainError("Resumed OOS window was not funded from prior terminal equity")
    complete = True
    for window in windows[start_index:]:
        if cancelled is not None and cancelled():
            complete = False
            break
        # This capability intentionally contains no OOS timestamp or OOS identity.
        training = TrainingContext(
            window.index, window.train_start, window.train_end, spec.datasets, spec.fingerprint
        )
        selection = optimizer(training)
        if not isinstance(selection, FrozenSelection):
            raise DomainError("IS optimizer must return a typed frozen M9 selection")
        _check_manifest_identity(
            selection.selected_manifest,
            spec,
            start=window.train_start,
            end=window.train_end,
        )
        result = execute_oos(window, selection)
        if not isinstance(result, OOSRun):
            raise DomainError("OOS executor must return a typed M8/M5 OOS run")
        row = WalkForwardWindowResult(
            window,
            selection,
            result,
            _deltas(selection.metrics, result.metrics),
        )
        _verify_pair(row, spec, window)
        prior_equity = rows[-1].oos_run.result.nav[-1].nav if rows else None
        expected_initial = _pinned_initial_capital(spec) if prior_equity is None else prior_equity
        if result.manifest.config.initial_capital != expected_initial:
            raise DomainError("OOS window funding differs from the walk-forward capital policy")
        if any(position.quantity != 0 for position in result.result.nav[-1].positions):
            raise DomainError("OOS executor must liquidate all positions within the test window")
        if any(
            order.status in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED)
            for order in result.result.orders
        ):
            raise DomainError("OOS executor must cancel or complete all orders at the boundary")
        recorder(row)
        rows.append(row)
    completed_rows = tuple(rows)
    return WalkForwardResult(
        spec.walk_forward_id,
        spec.fingerprint,
        completed_rows,
        stitch_oos_equity(completed_rows),
        complete and len(completed_rows) == len(windows),
    )
