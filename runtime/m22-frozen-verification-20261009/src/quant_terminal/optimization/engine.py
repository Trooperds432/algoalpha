"""Bounded, deterministic parameter search over an explicit trusted evaluator."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from time import perf_counter
from types import MappingProxyType
from typing import cast

from quant_terminal.analytics.metrics import Metric
from quant_terminal.core.exceptions import DomainError
from quant_terminal.experiments.models import canonical_json

from .models import OptimizationSpec, TrialScore, enumerate_candidates, score_metrics


@dataclass(frozen=True, slots=True)
class TrialEvaluation:
    """A completed run's metrics and M8 identity supplied by a trusted adapter."""

    metrics: Mapping[str, Metric]
    run_id: str
    input_fingerprint: str
    result_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.metrics, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, Metric)
            for key, value in self.metrics.items()
        ):
            raise DomainError("Trial metrics must be named M6 Metric values")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise DomainError("Completed trials require M8 run and fingerprint identities")
        for value in (self.input_fingerprint, self.result_fingerprint):
            if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
                raise DomainError("Completed trials require SHA-256 fingerprints")


@dataclass(frozen=True, slots=True)
class OptimizationTrial:
    index: int
    specification_fingerprint: str
    parameters: Mapping[str, object]
    status: str
    score: TrialScore | None
    metrics: Mapping[str, Metric]
    run_id: str | None
    input_fingerprint: str | None
    result_fingerprint: str | None
    elapsed_seconds: Decimal
    error: str | None = None

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise DomainError("Trial index must be nonnegative")
        if (
            not isinstance(self.specification_fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.specification_fingerprint) is None
        ):
            raise DomainError("Trial requires its optimization specification fingerprint")
        if self.status not in {"eligible", "infeasible", "failed"}:
            raise DomainError("Unsupported optimization trial status")
        if not isinstance(self.parameters, Mapping):
            raise DomainError("Trial parameters must be a mapping")
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))
        if not isinstance(self.metrics, Mapping) or any(
            not isinstance(name, str) or not isinstance(metric, Metric)
            for name, metric in self.metrics.items()
        ):
            raise DomainError("Trial analytics must be named typed metrics")
        object.__setattr__(self, "metrics", MappingProxyType(dict(self.metrics)))
        if (
            not isinstance(self.elapsed_seconds, Decimal)
            or not self.elapsed_seconds.is_finite()
            or self.elapsed_seconds < 0
        ):
            raise DomainError("Trial duration must be finite and nonnegative")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible, non-executable trial record."""
        data = {
            "index": self.index,
            "specification_fingerprint": self.specification_fingerprint,
            "parameters": dict(self.parameters),
            "status": self.status,
            "score": None
            if self.score is None
            else {
                "eligible": self.score.eligible,
                "objective": self.score.objective,
                "reasons": self.score.reasons,
                "constraint_values": self.score.constraint_values,
            },
            "metrics": {
                name: {
                    "value": metric.value,
                    "unit": metric.unit,
                    "observations": metric.observations,
                    "status": metric.status,
                    "reason": metric.reason,
                }
                for name, metric in self.metrics.items()
            },
            "run_id": self.run_id,
            "input_fingerprint": self.input_fingerprint,
            "result_fingerprint": self.result_fingerprint,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
        }
        return cast(dict[str, object], json.loads(canonical_json(data)))


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    optimization_id: str
    specification_fingerprint: str
    direction: str
    trials: tuple[OptimizationTrial, ...]
    completed: bool

    @property
    def ranked_eligible_trials(self) -> tuple[OptimizationTrial, ...]:
        """Eligible in-sample outcomes ordered by the declared objective direction."""
        scored = [
            trial
            for trial in self.trials
            if trial.status == "eligible"
            and trial.score is not None
            and trial.score.objective is not None
        ]

        def objective(trial: OptimizationTrial) -> Decimal:
            assert trial.score is not None and trial.score.objective is not None
            return trial.score.objective

        return tuple(
            sorted(
                scored,
                key=objective,
                reverse=self.direction == "maximize",
            )
        )


class TrialExecutionError(Exception):
    """Expected candidate-local evaluation failure; safe to retain and continue."""


Evaluator = Callable[[int, Mapping[str, object]], TrialEvaluation]
TrialRecorder = Callable[[OptimizationTrial], None]


def run_optimization(
    spec: OptimizationSpec,
    evaluator: Evaluator,
    recorder: TrialRecorder,
    *,
    start_index: int = 0,
    existing: tuple[OptimizationTrial, ...] = (),
    cancelled: Callable[[], bool] | None = None,
) -> OptimizationResult:
    """Run candidates serially, recording every returned or trial-local failed result.

    Evaluator owns one trusted M5/M6/M7 run and must durably register successes
    in M8 before returning. Recorder durably retains every optimization trial,
    including evaluator failures. Infrastructure/identity/storage exceptions
    from recorder fail closed and stop the campaign.
    """
    candidates = enumerate_candidates(spec)
    if not callable(evaluator) or not callable(recorder):
        raise DomainError("Explicit evaluator and durable trial recorder callbacks are required")
    if type(start_index) is not int or not 0 <= start_index <= len(candidates):
        raise DomainError("Invalid optimization resume cursor")
    trials = list(existing)
    if len(trials) != start_index or any(
        trial.index != index or trial.specification_fingerprint != spec.fingerprint
        for index, trial in enumerate(trials)
    ):
        raise DomainError("Resume requires a contiguous prefix of existing trial indices")
    for index, trial in enumerate(trials):
        if dict(trial.parameters) != dict(candidates[index]):
            raise DomainError("Resume candidates do not match the immutable specification")
    completed = True
    for index in range(start_index, len(candidates)):
        if cancelled is not None and cancelled():
            completed = False
            break
        started = perf_counter()
        parameters = candidates[index]
        try:
            evaluation = evaluator(index, parameters)
            if not isinstance(evaluation, TrialEvaluation):
                raise DomainError("Evaluator must return a typed TrialEvaluation")
            score = score_metrics(evaluation.metrics, spec)
            trial = OptimizationTrial(
                index,
                spec.fingerprint,
                parameters,
                "eligible" if score.eligible else "infeasible",
                score,
                evaluation.metrics,
                evaluation.run_id,
                evaluation.input_fingerprint,
                evaluation.result_fingerprint,
                Decimal(str(perf_counter() - started)),
                None if score.eligible else "; ".join(score.reasons),
            )
        except TrialExecutionError as exc:
            trial = OptimizationTrial(
                index,
                spec.fingerprint,
                parameters,
                "failed",
                None,
                {},
                None,
                None,
                None,
                Decimal(str(perf_counter() - started)),
                f"{type(exc).__name__}: {exc}",
            )
        recorder(trial)
        trials.append(trial)
    return OptimizationResult(
        spec.optimization_id, spec.fingerprint, spec.direction.value, tuple(trials), completed
    )
