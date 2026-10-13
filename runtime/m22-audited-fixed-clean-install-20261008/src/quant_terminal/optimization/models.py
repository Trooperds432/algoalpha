"""Immutable parameter-search specifications, candidate enumeration and trial scoring."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType

from quant_terminal.analytics.metrics import Metric
from quant_terminal.core.exceptions import DomainError


def _scalar(value: object) -> object:
    if type(value) in (str, int, bool) or value is None:
        return value
    if isinstance(value, Decimal) and value.is_finite():
        if not value:
            return "0"
        parts = value.as_tuple()
        digits: list[int] = list(parts.digits)
        exponent = int(parts.exponent)
        while digits[-1] == 0:
            digits.pop()
            exponent += 1
        return str(Decimal((parts.sign, tuple(digits), exponent)))
    raise DomainError("Parameter choices must be strings, integers, booleans, or finite Decimals")


def _canonical(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_canonical(x) for x in value]
    if isinstance(value, Decimal):
        return _scalar(value)
    if value is None or type(value) in (str, int, bool):
        return value
    raise DomainError(f"Unsupported optimization identity value: {type(value).__name__}")


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise DomainError("Optimization identity keys must be strings")
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    return _scalar(value)


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    frozen = _freeze(value)
    assert isinstance(frozen, Mapping)
    return frozen


class SearchMethod(StrEnum):
    GRID = "grid"
    RANDOM = "random"


class Direction(StrEnum):
    MAXIMIZE = "maximize"
    MINIMIZE = "minimize"


class Bound(StrEnum):
    MINIMUM = "minimum"
    MAXIMUM = "maximum"


@dataclass(frozen=True, slots=True)
class ParameterDimension:
    name: str
    choices: tuple[object, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise DomainError("Parameter dimension names must be nonempty strings")
        if not isinstance(self.choices, (tuple, list)) or not self.choices:
            raise DomainError("Parameter dimensions require a nonempty finite choice list")
        choices = tuple(_scalar(value) for value in self.choices)
        identities = [json.dumps(_canonical(value), sort_keys=True) for value in choices]
        if len(identities) != len(set(identities)):
            raise DomainError(f"Parameter choices must be unique: {self.name}")
        object.__setattr__(self, "choices", choices)


@dataclass(frozen=True, slots=True)
class MetricConstraint:
    metric: str
    bound: Bound
    threshold: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.metric, str) or not self.metric.strip():
            raise DomainError("Constraint metric must be a nonempty string")
        if not isinstance(self.bound, Bound):
            raise DomainError("Constraint bound must be minimum or maximum")
        if not isinstance(self.threshold, Decimal) or not self.threshold.is_finite():
            raise DomainError("Constraint threshold must be a finite Decimal")


@dataclass(frozen=True, slots=True)
class OptimizationSpec:
    optimization_id: str
    base_identity: Mapping[str, object]
    dimensions: tuple[ParameterDimension, ...]
    method: SearchMethod
    candidate_budget: int
    seed: int
    objective_metric: str
    direction: Direction
    constraints: tuple[MetricConstraint, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.optimization_id, str) or not self.optimization_id.strip():
            raise DomainError("optimization_id must be nonempty")
        if not isinstance(self.base_identity, Mapping) or not self.base_identity:
            raise DomainError("Base source/data/config identity is required")
        identity = _freeze_mapping(self.base_identity)
        object.__setattr__(self, "base_identity", identity)
        if not isinstance(self.dimensions, (tuple, list)) or not self.dimensions:
            raise DomainError("At least one parameter dimension is required")
        dims = tuple(self.dimensions)
        if any(not isinstance(dimension, ParameterDimension) for dimension in dims):
            raise DomainError("Dimensions must be typed ParameterDimension records")
        names = [dimension.name for dimension in dims]
        if len(names) != len(set(names)):
            raise DomainError("Parameter dimension names must be unique")
        object.__setattr__(self, "dimensions", tuple(sorted(dims, key=lambda d: d.name)))
        if not isinstance(self.method, SearchMethod) or not isinstance(self.direction, Direction):
            raise DomainError("Search method and objective direction must be explicit enums")
        if type(self.candidate_budget) is not int or not 1 <= self.candidate_budget <= 100_000:
            raise DomainError("candidate_budget must be in [1, 100000]")
        if type(self.seed) is not int or not 0 <= self.seed < 2**64:
            raise DomainError("seed must be unsigned 64-bit")
        if not isinstance(self.objective_metric, str) or not self.objective_metric.strip():
            raise DomainError("Objective metric must be nonempty")
        constraints = tuple(self.constraints)
        if any(not isinstance(item, MetricConstraint) for item in constraints):
            raise DomainError("Constraints must be typed MetricConstraint records")
        object.__setattr__(self, "constraints", constraints)
        if self.method is SearchMethod.GRID and self.candidate_count > self.candidate_budget:
            raise DomainError("Cartesian search space exceeds candidate_budget")
        if self.candidate_budget > self.candidate_count:
            raise DomainError("candidate_budget exceeds the number of unique candidates")

    @property
    def candidate_count(self) -> int:
        count = math.prod(len(dimension.choices) for dimension in self.dimensions)
        return count

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(_canonical(self.to_dict()), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    def to_dict(self) -> dict[str, object]:
        """Return the complete immutable search specification as canonical data."""
        return {
            "sampler_algorithm": "sha256-index-rejection-v1",
            "optimization_id": self.optimization_id,
            "base_identity": self.base_identity,
            "dimensions": [
                {"name": dimension.name, "choices": dimension.choices}
                for dimension in self.dimensions
            ],
            "method": self.method.value,
            "candidate_budget": self.candidate_budget,
            "seed": self.seed,
            "objective_metric": self.objective_metric,
            "direction": self.direction.value,
            "constraints": [
                {"metric": c.metric, "bound": c.bound.value, "threshold": c.threshold}
                for c in self.constraints
            ],
        }


def enumerate_candidates(spec: OptimizationSpec) -> tuple[Mapping[str, object], ...]:
    """Produce a stable complete grid or seeded random sample without replacement."""
    if not isinstance(spec, OptimizationSpec):
        raise DomainError("An OptimizationSpec is required")
    choices = [dimension.choices for dimension in spec.dimensions]
    if spec.method is SearchMethod.GRID:
        rows: Iterable[tuple[object, ...]] = itertools.product(*choices)
    else:
        indexes: set[int] = set()
        counter = 0
        bit_count = spec.candidate_count.bit_length()
        byte_count = (bit_count + 7) // 8
        mask = (1 << bit_count) - 1
        while len(indexes) < spec.candidate_budget:
            material = bytearray()
            block = 0
            while len(material) < byte_count:
                token = f"algoalpha-grid-sampler-v1:{spec.seed}:{counter}:{block}"
                material.extend(hashlib.sha256(token.encode("ascii")).digest())
                block += 1
            index = int.from_bytes(material[:byte_count], "big") & mask
            counter += 1
            if index < spec.candidate_count:
                indexes.add(index)
        ordered_indexes = sorted(indexes)
        rows_list: list[tuple[object, ...]] = []
        for index in ordered_indexes:
            remainder = index
            row: list[object] = [None] * len(choices)
            for position in range(len(choices) - 1, -1, -1):
                remainder, choice = divmod(remainder, len(choices[position]))
                row[position] = choices[position][choice]
            rows_list.append(tuple(row))
        rows = iter(rows_list)
    return tuple(
        MappingProxyType(
            {dimension.name: value for dimension, value in zip(spec.dimensions, row, strict=True)}
        )
        for row in rows
    )


@dataclass(frozen=True, slots=True)
class TrialScore:
    eligible: bool
    objective: Decimal | None
    reasons: tuple[str, ...]
    constraint_values: tuple[tuple[str, Decimal | None, bool], ...]


def score_metrics(metrics: Mapping[str, Metric], spec: OptimizationSpec) -> TrialScore:
    """Score explicit M6 metrics; unavailable objective/constraints remain ineligible."""
    objective_metric = metrics.get(spec.objective_metric)
    objective = None if objective_metric is None else objective_metric.value
    reasons: list[str] = []
    if objective is None:
        reason = (
            "missing objective metric"
            if objective_metric is None
            else (objective_metric.reason or "objective metric unavailable")
        )
        reasons.append(f"{spec.objective_metric}: {reason}")
    checks: list[tuple[str, Decimal | None, bool]] = []
    for constraint in spec.constraints:
        metric = metrics.get(constraint.metric)
        value = None if metric is None else metric.value
        passed = False
        if value is not None:
            passed = (
                value >= constraint.threshold
                if constraint.bound is Bound.MINIMUM
                else value <= constraint.threshold
            )
        if not passed:
            why = (
                "missing"
                if metric is None
                else metric.reason or "unavailable"
                if value is None
                else f"{value} violates {constraint.bound.value}"
            )
            reasons.append(f"{constraint.metric}: {why} {constraint.threshold}")
        checks.append((constraint.metric, value, passed))
    return TrialScore(not reasons, objective, tuple(reasons), tuple(checks))
