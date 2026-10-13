"""Deterministic tables for parameter surfaces and metric distributions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from quant_terminal.core.exceptions import DomainError

from .engine import OptimizationTrial


def _coordinate_key(value: object) -> tuple[int, object, str]:
    if type(value) is bool:
        return (0, int(value), "bool")
    if type(value) is int:
        return (1, Decimal(value), "int")
    try:
        numeric = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return (2, str(value), type(value).__name__)
    if numeric.is_finite() and isinstance(value, (str, Decimal)):
        return (1, numeric, type(value).__name__)
    return (2, str(value), type(value).__name__)


@dataclass(frozen=True, slots=True)
class SurfacePoint:
    trial_index: int
    parameters: tuple[tuple[str, object], ...]
    metric: str
    value: Decimal | None
    status: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class MetricDistribution:
    metric: str
    observations: tuple[Decimal, ...]
    unavailable_count: int
    failed_count: int


@dataclass(frozen=True, slots=True)
class ParameterDistribution:
    parameter: str
    frequencies: tuple[tuple[object, int], ...]


def parameter_distribution(
    trials: tuple[OptimizationTrial, ...], parameter: str
) -> ParameterDistribution:
    """Count candidate values, retaining candidates that failed evaluation."""
    if not isinstance(parameter, str) or not parameter.strip():
        raise DomainError("Distribution parameter must be nonempty")
    counts: dict[object, int] = {}
    for trial in trials:
        if parameter not in trial.parameters:
            raise DomainError("Requested distribution parameter is absent from a trial")
        value = trial.parameters[parameter]
        counts[value] = counts.get(value, 0) + 1
    return ParameterDistribution(
        parameter,
        tuple(sorted(counts.items(), key=lambda item: (type(item[0]).__name__, str(item[0])))),
    )


def parameter_surface(
    trials: tuple[OptimizationTrial, ...], metric: str, parameter_names: tuple[str, ...]
) -> tuple[SurfacePoint, ...]:
    """Return 1D/2D response-surface rows, retaining missing and failed trials."""
    if not isinstance(metric, str) or not metric.strip():
        raise DomainError("Surface metric must be nonempty")
    if len(parameter_names) not in (1, 2) or len(set(parameter_names)) != len(parameter_names):
        raise DomainError("Surface requires one or two unique parameter dimensions")
    points: list[SurfacePoint] = []
    for trial in sorted(trials, key=lambda item: item.index):
        if any(name not in trial.parameters for name in parameter_names):
            raise DomainError("Requested surface parameter is absent from a trial")
        metric_value = trial.metrics.get(metric)
        value = None if metric_value is None else metric_value.value
        reason = None
        if metric_value is None:
            reason = "metric not recorded"
        elif value is None:
            reason = metric_value.reason or "metric unavailable"
        elif trial.status == "failed":
            value = None
            reason = trial.error or "trial failed"
        points.append(
            SurfacePoint(
                trial.index,
                tuple((name, trial.parameters[name]) for name in parameter_names),
                metric,
                value,
                trial.status,
                reason,
            )
        )
    return tuple(points)


def metric_distribution(trials: tuple[OptimizationTrial, ...], metric: str) -> MetricDistribution:
    """Return ordered available values and explicit missing/failed counts."""
    if not isinstance(metric, str) or not metric.strip():
        raise DomainError("Distribution metric must be nonempty")
    values: list[Decimal] = []
    unavailable = failed = 0
    for trial in sorted(trials, key=lambda item: item.index):
        if trial.status == "failed":
            failed += 1
            continue
        found = trial.metrics.get(metric)
        if found is None or found.value is None:
            unavailable += 1
            continue
        values.append(found.value)
    return MetricDistribution(metric, tuple(values), unavailable, failed)


def response_matrix(
    points: tuple[SurfacePoint, ...], x_parameter: str, y_parameter: str
) -> tuple[tuple[object, ...], ...]:
    """Build a deterministic rectangular 2D table; absent cells remain None."""
    cells: dict[tuple[object, object], Decimal | None] = {}
    xs: set[object] = set()
    ys: set[object] = set()
    for point in points:
        params = dict(point.parameters)
        if x_parameter not in params or y_parameter not in params:
            raise DomainError("Response-matrix axes are absent from the surface")
        x, y = params[x_parameter], params[y_parameter]
        xs.add(x)
        ys.add(y)
        coordinate = (x, y)
        if coordinate in cells:
            raise DomainError("Response matrix contains duplicate parameter coordinates")
        cells[coordinate] = point.value

    ordered_x = sorted(xs, key=_coordinate_key)
    ordered_y = sorted(ys, key=_coordinate_key)
    matrix: list[tuple[object, ...]] = [(y_parameter, *ordered_x)]
    for y in ordered_y:
        matrix.append((y, *(cells.get((x, y)) for x in ordered_x)))
    return tuple(matrix)


def neighbor_metric_changes(points: tuple[SurfacePoint, ...]) -> tuple[Decimal, ...]:
    """Absolute metric changes between candidate pairs differing in one parameter.

    The result is a diagnostic distribution, not a robustness guarantee. Only
    adjacent observed levels with all other parameter values held fixed count.
    """
    if not points:
        return ()
    names = tuple(name for name, _ in points[0].parameters)
    if not names or any(tuple(name for name, _ in point.parameters) != names for point in points):
        raise DomainError("Neighbor analysis requires consistent parameter axes")
    coordinates = [dict(point.parameters) for point in points]
    changes: list[Decimal] = []
    for axis in names:
        groups: dict[tuple[tuple[str, object], ...], list[tuple[object, Decimal | None]]] = {}
        for point, coordinate in zip(points, coordinates, strict=True):
            others = tuple((name, coordinate[name]) for name in names if name != axis)
            groups.setdefault(others, []).append((coordinate[axis], point.value))
        for rows in groups.values():
            rows.sort(key=lambda item: _coordinate_key(item[0]))
            for (_, left), (_, right) in zip(rows, rows[1:], strict=False):
                if left is not None and right is not None:
                    changes.append(abs(right - left))
    return tuple(changes)
