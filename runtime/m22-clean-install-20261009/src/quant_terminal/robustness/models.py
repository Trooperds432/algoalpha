"""Immutable robustness perturbation and comparison records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from types import MappingProxyType

from quant_terminal.analytics.metrics import Metric
from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments import ExperimentManifest, canonical_hash, canonical_json
from quant_terminal.optimization.engine import OptimizationTrial


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise DomainError("Scenario object keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return value


class ScenarioKind(StrEnum):
    PARAMETER = "parameter"
    COMMISSION = "commission"
    SLIPPAGE = "slippage"
    BAR_DELAY = "bar_delay"
    START_DATE = "start_date"
    END_DATE = "end_date"
    SUBPERIOD = "subperiod"
    REGIME = "regime"
    UNIVERSE = "universe"


_PROTECTED_FIELDS = frozenset(
    {
        "currency",
        "strategy_id",
        "strategy_source_hash",
        "project_source_hash",
        "datasets",
        "dataset_snapshot_id",
        "dependency_lock_hash",
        "engine_version",
        "benchmark",
    }
)

_SCENARIO_FIELDS = {
    ScenarioKind.PARAMETER: ("parameters.",),
    ScenarioKind.COMMISSION: ("execution_assumptions.commission",),
    ScenarioKind.SLIPPAGE: ("execution_assumptions.slippage",),
    ScenarioKind.BAR_DELAY: ("bar_delay",),
    ScenarioKind.START_DATE: ("start",),
    ScenarioKind.END_DATE: ("end",),
    ScenarioKind.SUBPERIOD: ("start", "end"),
    ScenarioKind.REGIME: ("regime.",),
    ScenarioKind.UNIVERSE: ("universe.",),
}


@dataclass(frozen=True, slots=True)
class RobustnessScenario:
    scenario_id: str
    kind: ScenarioKind
    changes: tuple[tuple[str, object], ...]
    rationale: str

    def __post_init__(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id.strip():
            raise DomainError("scenario_id must be nonempty")
        if not isinstance(self.kind, ScenarioKind):
            raise DomainError("A supported scenario kind is required")
        if not isinstance(self.rationale, str) or not self.rationale.strip():
            raise DomainError("A scenario rationale is required")
        rows = tuple(self.changes)
        if not rows or any(
            not isinstance(row, tuple)
            or len(row) != 2
            or not isinstance(row[0], str)
            or not row[0].strip()
            for row in rows
        ):
            raise DomainError("Scenarios require named exact changes")
        names = tuple(name for name, _ in rows)
        if len(set(names)) != len(names):
            raise DomainError("Scenario change names must be unique")
        if any(_PROTECTED_FIELDS.intersection(name.split(".")) for name in names):
            raise DomainError("A robustness scenario may not change protected source/data identity")
        prefixes = _SCENARIO_FIELDS[self.kind]

        def matches(name: str, prefix: str) -> bool:
            if prefix.endswith("."):
                return name.startswith(prefix) and len(name) > len(prefix)
            return name == prefix or name.startswith(prefix + ".")

        if any(not any(matches(name, prefix) for prefix in prefixes) for name in names):
            raise DomainError("Scenario change field does not match its declared category")
        if self.kind is ScenarioKind.BAR_DELAY:
            delay = dict(rows).get("bar_delay")
            if type(delay) is not int or delay < 0:
                raise DomainError("bar_delay must be a nonnegative integer number of bars")
        normalized = tuple(
            sorted((name, _freeze(json.loads(canonical_json(value)))) for name, value in rows)
        )
        object.__setattr__(self, "changes", normalized)


@dataclass(frozen=True, slots=True)
class RobustnessSpec:
    analysis_id: str
    baseline: ExperimentManifest
    scenarios: tuple[RobustnessScenario, ...]
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.analysis_id, str) or not self.analysis_id.strip():
            raise DomainError("analysis_id must be nonempty")
        if not isinstance(self.baseline, ExperimentManifest):
            raise DomainError("A registered M8 baseline manifest is required")
        scenarios = tuple(self.scenarios)
        if not scenarios or any(not isinstance(item, RobustnessScenario) for item in scenarios):
            raise DomainError("At least one typed robustness scenario is required")
        if len({item.scenario_id for item in scenarios}) != len(scenarios):
            raise DomainError("Scenario identities must be unique")
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise DomainError("Unsupported robustness schema version")
        object.__setattr__(self, "scenarios", scenarios)

    @property
    def fingerprint(self) -> str:
        return canonical_hash(
            {
                "schema_version": self.schema_version,
                "analysis_id": self.analysis_id,
                "baseline_run_id": self.baseline.run_id.value,
                "baseline_input_fingerprint": self.baseline.input_fingerprint,
                "baseline_result_fingerprint": self.baseline.result_fingerprint,
                "baseline_currency": self.baseline.config.currency,
                "scenarios": [
                    {
                        "scenario_id": item.scenario_id,
                        "kind": item.kind.value,
                        "changes": item.changes,
                        "rationale": item.rationale,
                    }
                    for item in self.scenarios
                ],
            }
        )


@dataclass(frozen=True, slots=True)
class MetricDelta:
    metric: str
    unit: str
    absolute: Decimal | None
    relative: Decimal | None
    status: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class ScenarioComparison:
    scenario_id: str
    run_id: str | None
    status: str
    deltas: tuple[MetricDelta, ...]
    reason: str | None


def _delta(name: str, baseline: Metric | None, candidate: Metric | None) -> MetricDelta:
    if baseline is None or baseline.value is None or baseline.status != "available":
        reason = (
            "baseline metric unavailable"
            if baseline is None or not baseline.reason
            else baseline.reason
        )
        return MetricDelta(
            name,
            "unknown" if baseline is None else baseline.unit,
            None,
            None,
            "unavailable",
            reason,
        )
    if candidate is None or candidate.value is None or candidate.status != "available":
        reason = (
            "scenario metric unavailable"
            if candidate is None or not candidate.reason
            else candidate.reason
        )
        unit = baseline.unit if candidate is None else candidate.unit
        return MetricDelta(name, unit, None, None, "unavailable", reason)
    if baseline.unit != candidate.unit:
        return MetricDelta(name, baseline.unit, None, None, "unavailable", "metric units differ")
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        absolute = candidate.value - baseline.value
        relative = None if baseline.value == 0 else absolute / baseline.value
    return MetricDelta(
        name,
        baseline.unit,
        absolute,
        relative,
        "available",
        "relative delta unavailable for zero baseline" if relative is None else None,
    )


def compare_scenario_metrics(
    scenario: RobustnessScenario,
    baseline: Mapping[str, Metric],
    candidate: Mapping[str, Metric],
    *,
    run_id: str | None,
    status: str = "completed",
    reason: str | None = None,
) -> ScenarioComparison:
    """Compare named M6 metrics without coercing unavailable values to zero."""
    if status not in {"completed", "failed", "infeasible", "unavailable"}:
        raise DomainError("Unsupported scenario outcome status")
    if status != "completed":
        return ScenarioComparison(scenario.scenario_id, run_id, status, (), reason)
    names = tuple(sorted(set(baseline) | set(candidate)))
    if any(not isinstance(value, Metric) for value in (*baseline.values(), *candidate.values())):
        raise DomainError("Scenario comparisons require typed M6 metrics")
    if any(
        metric.value is not None
        and (not isinstance(metric.value, Decimal) or not metric.value.is_finite())
        for metric in (*baseline.values(), *candidate.values())
    ):
        raise DomainError("Scenario metric values must be finite Decimals or null")
    return ScenarioComparison(
        scenario.scenario_id,
        run_id,
        status,
        tuple(_delta(name, baseline.get(name), candidate.get(name)) for name in names),
        reason,
    )


def _numeric(value: object) -> Decimal | None:
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if type(value) is int:
        return Decimal(value)
    if isinstance(value, str) and value.strip():
        try:
            numeric = Decimal(value)
        except ArithmeticError:
            return None
        return numeric if numeric.is_finite() else None
    return None


def _same_level(left: object, right: object) -> bool:
    left_numeric, right_numeric = _numeric(left), _numeric(right)
    if left_numeric is not None and right_numeric is not None:
        return left_numeric == right_numeric
    return type(left) is type(right) and left == right


def parameter_neighborhood(
    trials: tuple[OptimizationTrial, ...],
    parameter: str,
    *,
    selected_value: object,
    metric: str,
    radius: int = 1,
    fixed_parameters: Mapping[str, object] | None = None,
    declared_levels: tuple[object, ...] | None = None,
) -> tuple[tuple[object, Decimal | None, str], ...]:
    """Summarize a numeric grid neighborhood, retaining declared missing cells.

    ``declared_levels`` preserves the explicit grid order. Without it, observed
    integer, Decimal, and numeric-string levels are sorted numerically.
    """
    if (
        not isinstance(parameter, str)
        or not parameter.strip()
        or not isinstance(metric, str)
        or not metric.strip()
        or type(radius) is not int
        or radius < 0
    ):
        raise DomainError("Invalid parameter-neighborhood request")
    fixed = dict(fixed_parameters or {})
    if parameter in fixed:
        raise DomainError("Fixed parameters may not include the neighborhood axis")
    fingerprints = {trial.specification_fingerprint for trial in trials}
    indexes = [trial.index for trial in trials]
    if len(fingerprints) > 1 or len(indexes) != len(set(indexes)):
        raise DomainError("Neighborhood trials must have one campaign identity and unique indices")
    selected_trials = tuple(
        trial
        for trial in trials
        if parameter in trial.parameters
        and all(trial.parameters.get(name) == value for name, value in fixed.items())
    )
    if declared_levels is None:
        levels: list[object] = []
        for trial in selected_trials:
            value = trial.parameters[parameter]
            if not any(_same_level(value, existing) for existing in levels):
                levels.append(value)
        levels.sort(key=lambda value: (_numeric(value) is None, _numeric(value)))
    else:
        if not isinstance(declared_levels, (tuple, list)) or not declared_levels:
            raise DomainError("Declared parameter levels must be a nonempty finite sequence")
        levels = list(declared_levels)
        numeric_levels = [_numeric(level) for level in levels]
        if any(value is None for value in numeric_levels):
            raise DomainError("Parameter neighborhoods require numeric finite-grid levels")
        if len(set(numeric_levels)) != len(numeric_levels):
            raise DomainError("Declared parameter levels must be numerically unique")
        if any(
            not any(_same_level(trial.parameters[parameter], level) for level in levels)
            for trial in selected_trials
        ):
            raise DomainError("Observed trial level is absent from the declared grid")
    center = _numeric(selected_value)
    if center is None or any(_numeric(level) is None for level in levels):
        raise DomainError("Parameter neighborhoods require numeric finite-grid levels")
    if not any(_same_level(selected_value, level) for level in levels):
        raise DomainError("Selected parameter value is absent from the trial grid")
    index = next(i for i, level in enumerate(levels) if _same_level(level, selected_value))
    included = levels[max(0, index - radius) : index + radius + 1]
    rows: list[tuple[object, Decimal | None, str]] = []
    for level in levels:
        if not any(_same_level(level, included_level) for included_level in included):
            continue
        matching = [
            trial
            for trial in selected_trials
            if _same_level(trial.parameters.get(parameter), level)
        ]
        if not matching and declared_levels is not None:
            rows.append((level, None, "missing"))
            continue
        if len(matching) != 1:
            raise DomainError("Each neighborhood level must identify exactly one trial")
        trial = matching[0]
        metric_value = trial.metrics.get(metric)
        if (
            metric_value is not None
            and metric_value.value is not None
            and (not isinstance(metric_value.value, Decimal) or not metric_value.value.is_finite())
        ):
            raise DomainError("Neighborhood metric values must be finite Decimals or null")
        value = (
            None
            if metric_value is None or metric_value.status != "available"
            else metric_value.value
        )
        if trial.status == "failed":
            status = "failed"
            value = None
        elif trial.status == "infeasible":
            status = "infeasible"
        elif value is None:
            status = "unavailable"
        elif trial.status == "eligible":
            status = trial.status
        else:
            status = "unavailable"
        rows.append((level, value, status))
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class PlateauDiagnostic:
    threshold: Decimal
    direction: str
    center_value: Decimal | None
    status_counts: tuple[tuple[str, int], ...]
    stability_fraction: Decimal | None
    plateau_levels: tuple[object, ...]
    missing_cells: tuple[object, ...]
    classification: str
    reason: str | None


def diagnose_plateau(
    rows: tuple[tuple[object, Decimal | None, str], ...],
    *,
    selected_value: object,
    threshold: Decimal,
    direction: str,
    minimum_plateau_levels: int = 3,
) -> PlateauDiagnostic:
    """Classify the selected local optimum against its adjacent grid levels.

    ``threshold`` is the maximum tolerated absolute metric deviation from the
    selected level. Only eligible, available cells count toward a plateau.
    Missing, failed, infeasible, and unavailable cells remain visible and break
    contiguity. The result is a sensitivity diagnostic, not a selection rule.
    """
    if not isinstance(threshold, Decimal) or not threshold.is_finite() or threshold < 0:
        raise DomainError("Plateau threshold must be a nonnegative finite Decimal")
    if direction not in ("maximize", "minimize"):
        raise DomainError("Plateau direction must be maximize or minimize")
    if type(minimum_plateau_levels) is not int or minimum_plateau_levels < 2:
        raise DomainError("minimum_plateau_levels must be an integer of at least two")
    if not rows:
        raise DomainError("Plateau diagnostics require neighborhood rows")
    allowed_statuses = ("eligible", "failed", "infeasible", "unavailable", "missing")
    if any(status not in allowed_statuses for _, _, status in rows):
        raise DomainError("Unsupported neighborhood status")
    if any(
        value is not None and (not isinstance(value, Decimal) or not value.is_finite())
        for _, value, _ in rows
    ):
        raise DomainError("Plateau metric values must be finite Decimals or null")
    if len({_level_key(level) for level, _, _ in rows}) != len(rows):
        raise DomainError("Neighborhood levels must be unique")
    counts = {status: 0 for status in allowed_statuses}
    for _, _, status in rows:
        counts[status] += 1
    status_counts = tuple((status, counts[status]) for status in allowed_statuses)
    missing = tuple(level for level, _, status in rows if status == "missing")
    selected_index = next(
        (index for index, (level, _, _) in enumerate(rows) if _same_level(level, selected_value)),
        None,
    )
    if selected_index is None:
        return PlateauDiagnostic(
            threshold,
            direction,
            None,
            status_counts,
            None,
            (),
            missing,
            "inconclusive",
            "selected level is absent from neighborhood",
        )
    _, center_value, center_status = rows[selected_index]
    if center_status != "eligible" or center_value is None:
        return PlateauDiagnostic(
            threshold,
            direction,
            center_value,
            status_counts,
            None,
            (),
            missing,
            "unavailable",
            "selected metric is not an available eligible outcome",
        )

    def within(value: Decimal | None, status: str) -> bool:
        if status != "eligible" or value is None:
            return False
        value_num, value_den = value.as_integer_ratio()
        center_num, center_den = center_value.as_integer_ratio()
        threshold_num, threshold_den = threshold.as_integer_ratio()
        difference_num = value_num * center_den - center_num * value_den
        difference_den = value_den * center_den
        return abs(difference_num) * threshold_den <= threshold_num * difference_den

    qualifying = [within(value, status) for _, value, status in rows]
    neighbor_indices = [index for index in range(len(rows)) if index != selected_index]
    available_neighbors = [
        index
        for index in neighbor_indices
        if rows[index][2] == "eligible" and rows[index][1] is not None
    ]
    available_count = len(available_neighbors)
    qualifying_count = sum(qualifying[index] for index in available_neighbors)
    with localcontext(Context(prec=34, rounding=ROUND_HALF_EVEN)):
        stability_fraction = (
            Decimal(qualifying_count) / Decimal(available_count) if available_count else None
        )
    left = right = selected_index
    while left > 0 and qualifying[left - 1]:
        left -= 1
    while right + 1 < len(rows) and qualifying[right + 1]:
        right += 1
    plateau_levels = tuple(level for level, _, _ in rows[left : right + 1])
    width = len(plateau_levels)
    adjacent_indices = [
        index for index in (selected_index - 1, selected_index + 1) if 0 <= index < len(rows)
    ]

    def is_better_neighbor(index: int) -> bool:
        value = rows[index][1]
        if rows[index][2] != "eligible" or value is None:
            return False
        return value > center_value if direction == "maximize" else value < center_value

    has_better_neighbor = any(is_better_neighbor(index) for index in adjacent_indices)
    if has_better_neighbor:
        classification, reason = "non_optimal_center", "an adjacent eligible level scores better"
    elif width >= minimum_plateau_levels:
        classification, reason = "broad_plateau", None
    elif width == 1:
        if adjacent_indices and all(
            rows[i][2] == "eligible" and rows[i][1] is not None for i in adjacent_indices
        ):
            classification, reason = "isolated_optimum", None
        else:
            classification, reason = "inconclusive", "adjacent cells are missing or unavailable"
    else:
        classification, reason = "narrow_plateau", None
    return PlateauDiagnostic(
        threshold,
        direction,
        center_value,
        status_counts,
        stability_fraction,
        plateau_levels,
        missing,
        classification,
        reason,
    )


def _level_key(value: object) -> tuple[str, object]:
    numeric = _numeric(value)
    return ("numeric", numeric) if numeric is not None else (type(value).__name__, repr(value))
