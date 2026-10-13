"""Performance and risk analytics over immutable backtest results."""

from .metrics import (
    AnalyticsConfig,
    AnalyticsReport,
    ClosedTrade,
    Metric,
    RiskOfRuinConfig,
    analyze,
    closed_trades,
    sampled_nav_points,
    sampled_returns,
    trade_excursions,
)

__all__ = [
    "AnalyticsConfig",
    "AnalyticsReport",
    "ClosedTrade",
    "Metric",
    "RiskOfRuinConfig",
    "analyze",
    "closed_trades",
    "sampled_nav_points",
    "sampled_returns",
    "trade_excursions",
]
