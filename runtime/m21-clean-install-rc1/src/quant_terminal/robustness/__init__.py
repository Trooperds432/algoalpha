"""Robustness and overfitting diagnostics over immutable research inputs."""

from .adapter import ObservationOverlay, ResearchScenarioAdapter
from .bootstrap import (
    BootstrapEstimator,
    BootstrapIntervalResult,
    BootstrapIntervalSpec,
    bootstrap_interval,
)
from .engine import RobustnessRun, ScenarioExecutionError, run_robustness, scenario_config
from .method_support import MethodPrerequisites, MethodSupportReport, method_support_report
from .models import (
    MetricDelta,
    PlateauDiagnostic,
    RobustnessScenario,
    RobustnessSpec,
    ScenarioComparison,
    ScenarioKind,
    compare_scenario_metrics,
    diagnose_plateau,
    parameter_neighborhood,
)
from .sharpe import PeriodicReturnSample, ProbabilisticSharpeResult, probabilistic_sharpe

__all__ = [
    "BootstrapEstimator",
    "BootstrapIntervalResult",
    "BootstrapIntervalSpec",
    "MethodPrerequisites",
    "MethodSupportReport",
    "MetricDelta",
    "ObservationOverlay",
    "PeriodicReturnSample",
    "PlateauDiagnostic",
    "ProbabilisticSharpeResult",
    "ResearchScenarioAdapter",
    "RobustnessRun",
    "RobustnessScenario",
    "RobustnessSpec",
    "ScenarioComparison",
    "ScenarioExecutionError",
    "ScenarioKind",
    "bootstrap_interval",
    "compare_scenario_metrics",
    "diagnose_plateau",
    "method_support_report",
    "parameter_neighborhood",
    "probabilistic_sharpe",
    "run_robustness",
    "scenario_config",
]
