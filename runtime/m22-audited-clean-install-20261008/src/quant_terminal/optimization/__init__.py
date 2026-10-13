"""Parameter optimization public API."""

from .analysis import (
    MetricDistribution,
    ParameterDistribution,
    SurfacePoint,
    metric_distribution,
    neighbor_metric_changes,
    parameter_distribution,
    parameter_surface,
    response_matrix,
)
from .engine import (
    OptimizationResult,
    OptimizationTrial,
    TrialEvaluation,
    TrialExecutionError,
    run_optimization,
)
from .journal import JournalIntegrityError, OptimizationJournal
from .models import (
    Bound,
    Direction,
    MetricConstraint,
    OptimizationSpec,
    ParameterDimension,
    SearchMethod,
    TrialScore,
    enumerate_candidates,
    score_metrics,
)

__all__ = [
    "Bound",
    "Direction",
    "MetricDistribution",
    "MetricConstraint",
    "JournalIntegrityError",
    "OptimizationResult",
    "OptimizationJournal",
    "OptimizationSpec",
    "OptimizationTrial",
    "ParameterDistribution",
    "ParameterDimension",
    "SearchMethod",
    "SurfacePoint",
    "TrialEvaluation",
    "TrialExecutionError",
    "TrialScore",
    "enumerate_candidates",
    "metric_distribution",
    "neighbor_metric_changes",
    "parameter_surface",
    "parameter_distribution",
    "response_matrix",
    "run_optimization",
    "score_metrics",
]
