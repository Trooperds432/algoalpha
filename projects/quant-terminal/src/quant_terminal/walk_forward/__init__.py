"""Walk-forward research and validation API."""

from .engine import (
    ParameterStability,
    WalkForwardResult,
    parameter_stability,
    plan_windows,
    run_walk_forward,
    stitch_oos_equity,
)
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

__all__ = [
    "FrozenSelection",
    "MetricDelta",
    "OOSRun",
    "ParameterStability",
    "StitchedEquityPoint",
    "TrainingContext",
    "WalkForwardResult",
    "WalkForwardSpec",
    "WalkForwardWindow",
    "WalkForwardWindowResult",
    "WindowMode",
    "plan_windows",
    "parameter_stability",
    "run_walk_forward",
    "stitch_oos_equity",
]
