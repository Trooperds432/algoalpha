"""Build complete report snapshots from immutable backtest results."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from decimal import Context, Decimal, localcontext

from quant_terminal.analytics import (
    AnalyticsConfig,
    AnalyticsReport,
    analyze,
    closed_trades,
    sampled_nav_points,
    sampled_returns,
    trade_excursions,
)
from quant_terminal.backtesting.models import BacktestResult, NAVPoint
from quant_terminal.core.exceptions import DomainError
from quant_terminal.reporting.models import (
    BacktestReport,
    Chart,
    ChartSeries,
    ReportTable,
    ReportValue,
)
from quant_terminal.reporting.utils import decimal_context, iso_utc


def _context() -> Context:
    return decimal_context()


def _iso(value: datetime | None) -> str | None:
    return iso_utc(value)


def _safe_metadata(metadata: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    sensitive = (
        "secret",
        "token",
        "password",
        "credential",
        "apikey",
        "privatekey",
        "secretkey",
        "accesskey",
        "signingkey",
        "authkey",
    )
    return tuple(
        (
            name,
            "[REDACTED]"
            if any(term in re.sub(r"[^a-z0-9]", "", name.casefold()) for term in sensitive)
            else value,
        )
        for name, value in metadata
    )


def _table(
    name: str, columns: tuple[str, ...], rows: Sequence[tuple[ReportValue, ...]]
) -> ReportTable:
    return ReportTable(name, columns, tuple(rows))


def _table_rows(result: BacktestResult, analytics: AnalyticsReport) -> tuple[ReportTable, ...]:
    metric_rows = [
        (name, metric.value, metric.unit, metric.observations, metric.status, metric.reason)
        for name, metric in analytics.metrics
    ]
    metric_map = dict(analytics.metrics)
    by_metric = dict(analytics.metrics)

    def selected_metrics(names: tuple[str, ...]) -> list[tuple[ReportValue, ...]]:
        return [
            (
                name,
                by_metric[name].value,
                by_metric[name].unit,
                by_metric[name].status,
                by_metric[name].reason,
            )
            for name in names
            if name in by_metric
        ]

    risk_names = (
        "annualized_volatility",
        "sharpe",
        "sortino",
        "calmar",
        "maximum_drawdown",
        "drawdown_duration_days",
        "drawdown_duration_sessions",
        "recovery_time_days",
        "ulcer_index",
        "omega",
        "var_95",
        "var_99",
        "var_999",
        "expected_shortfall_95",
        "expected_shortfall_99",
        "expected_shortfall_999",
        "skew",
        "excess_kurtosis",
        "tail_ratio_95_5",
        "risk_of_ruin",
    )
    trading_names = (
        "trade_count",
        "win_rate",
        "loss_rate",
        "breakeven_trades",
        "profit_factor",
        "expectancy",
        "payoff_ratio",
        "average_win",
        "average_loss",
        "median_trade",
        "best_trade",
        "worst_trade",
        "average_holding_days",
        "max_consecutive_wins",
        "max_consecutive_losses",
        "average_mae_per_unit",
        "average_mfe_per_unit",
    )
    exposure_names = (
        "average_gross_exposure",
        "average_net_exposure",
        "average_gross_leverage",
        "average_net_leverage",
        "turnover",
    )
    cost_rows = [
        (
            name,
            metric_map[name].value,
            metric_map[name].unit,
            metric_map[name].status,
            metric_map[name].reason,
        )
        for name in ("commissions", "slippage_cost", "total_cost")
    ]
    nav_rows = [
        (
            _iso(point.timestamp),
            point.nav,
            point.cash,
            point.period_return,
            point.realized_pnl,
            point.unrealized_pnl,
            point.long_exposure,
            point.short_exposure,
            point.gross_exposure,
            point.net_exposure,
            point.leverage,
            point.reconciliation_error,
        )
        for point in result.nav
    ]
    drawdown_rows = list(_drawdown_records(sampled_nav_points(result)))
    closed = closed_trades(result)
    excursions = trade_excursions(result)
    symbols = {fill.instrument.instrument_id.value: fill.instrument.symbol for fill in result.fills}
    trade_rows = []
    for index, (trade, (mae, mfe)) in enumerate(zip(closed, excursions, strict=True), start=1):
        trade_rows.append(
            (
                index,
                trade.instrument_id.value,
                symbols.get(trade.instrument_id.value, ""),
                "long" if trade.direction > 0 else "short",
                trade.quantity,
                _iso(trade.entry_timestamp),
                _iso(trade.exit_timestamp),
                _iso(trade.entry_observed_at),
                _iso(trade.exit_observed_at),
                trade.entry_price,
                trade.exit_price,
                trade.entry_notional,
                trade.gross,
                trade.entry_fee,
                trade.exit_fee,
                trade.net,
                trade.holding_days,
                mae,
                mfe,
            )
        )
    final_positions = result.nav[-1].positions if result.nav else ()
    position_rows = [
        (
            position.instrument_id.value,
            position.symbol,
            position.quantity,
            position.average_entry_price,
            position.mark_price,
            position.market_value,
            position.realized_pnl,
            position.unrealized_pnl,
        )
        for position in final_positions
    ]
    order_rows = []
    for order in result.orders:
        intent = order.intent
        events = [
            {
                "sequence": event.sequence,
                "status": event.status.value,
                "timestamp": _iso(event.timestamp),
                "reason": event.reason,
            }
            for event in order.events
        ]
        order_rows.append(
            (
                intent.order_id.value,
                intent.instrument.instrument_id.value,
                intent.instrument.symbol,
                intent.side.value,
                intent.order_type.value,
                intent.quantity,
                intent.limit_price,
                intent.stop_price,
                intent.max_execution_price,
                _iso(intent.created_at),
                _iso(intent.expires_at),
                order.status.value,
                _iso(order.accepted_at),
                order.filled_quantity,
                order.remaining_quantity,
                order.reason,
                json.dumps(events, ensure_ascii=False, separators=(",", ":")),
            )
        )
    fill_rows = [
        (
            fill.fill_id.value,
            fill.order_id.value,
            fill.instrument.instrument_id.value,
            fill.instrument.symbol,
            fill.side.value,
            fill.quantity,
            fill.reference_price,
            fill.price,
            fill.commission,
            _iso(fill.timestamp),
            _iso(fill.observed_at),
        )
        for fill in result.fills
    ]
    benchmark_rows = [
        (
            _iso(point.timestamp),
            point.instrument_id.value,
            point.price,
            point.value,
            point.price_return_only,
        )
        for point in result.benchmark
    ]
    exposure_rows = [
        (
            _iso(point.timestamp),
            point.long_exposure,
            point.short_exposure,
            point.gross_exposure,
            point.net_exposure,
            point.leverage,
            point.net_exposure / point.nav if point.nav > 0 else None,
        )
        for point in result.nav
    ]
    return (
        _table(
            "nav",
            (
                "timestamp",
                "nav",
                "cash",
                "period_return",
                "realized_pnl",
                "unrealized_pnl",
                "long_exposure",
                "short_exposure",
                "gross_exposure",
                "net_exposure",
                "gross_leverage",
                "reconciliation_error",
            ),
            nav_rows,
        ),
        _table(
            "metrics",
            ("name", "value", "unit", "observations", "status", "reason"),
            metric_rows,
        ),
        _table(
            "risk_metrics",
            ("name", "value", "unit", "status", "reason"),
            selected_metrics(risk_names),
        ),
        _table(
            "trading_statistics",
            ("name", "value", "unit", "status", "reason"),
            selected_metrics(trading_names),
        ),
        _table(
            "exposure_metrics",
            ("name", "value", "unit", "status", "reason"),
            selected_metrics(exposure_names),
        ),
        _table(
            "execution_costs",
            ("metric", "value", "unit", "status", "reason"),
            cost_rows,
        ),
        _table(
            "drawdowns",
            (
                "episode",
                "peak_timestamp",
                "trough_timestamp",
                "recovery_timestamp",
                "status",
                "depth",
                "peak_to_recovery_days",
                "peak_to_recovery_sessions",
            ),
            drawdown_rows,
        ),
        _table(
            "trades",
            (
                "trade_number",
                "instrument_id",
                "symbol",
                "direction",
                "quantity",
                "entry_timestamp",
                "exit_timestamp",
                "entry_observed_at",
                "exit_observed_at",
                "entry_price",
                "exit_price",
                "entry_notional",
                "gross_pnl",
                "entry_commission",
                "exit_commission",
                "net_pnl",
                "holding_days",
                "mae_per_unit",
                "mfe_per_unit",
            ),
            trade_rows,
        ),
        _table(
            "positions",
            (
                "instrument_id",
                "symbol",
                "quantity",
                "average_entry_price",
                "mark_price",
                "market_value",
                "realized_pnl",
                "unrealized_pnl",
            ),
            position_rows,
        ),
        _table(
            "orders",
            (
                "order_id",
                "instrument_id",
                "symbol",
                "side",
                "order_type",
                "quantity",
                "limit_price",
                "stop_price",
                "max_execution_price",
                "created_at",
                "expires_at",
                "status",
                "accepted_at",
                "filled_quantity",
                "remaining_quantity",
                "reason",
                "events_json",
            ),
            order_rows,
        ),
        _table(
            "fills",
            (
                "fill_id",
                "order_id",
                "instrument_id",
                "symbol",
                "side",
                "quantity",
                "reference_price",
                "price",
                "commission",
                "economic_timestamp",
                "observed_at",
            ),
            fill_rows,
        ),
        _table(
            "benchmark",
            ("timestamp", "instrument_id", "price", "normalized_price_value", "price_return_only"),
            benchmark_rows,
        ),
        _table(
            "exposure",
            (
                "timestamp",
                "long_exposure",
                "short_exposure",
                "gross_exposure",
                "net_exposure",
                "gross_leverage",
                "net_leverage",
            ),
            exposure_rows,
        ),
    )


def _period_returns(
    returns: tuple[tuple[datetime, Decimal], ...], by_year: bool
) -> tuple[tuple[str, Decimal], ...]:
    groups: dict[str, list[Decimal]] = defaultdict(list)
    with localcontext(_context()):
        for timestamp, value in returns:
            key = timestamp.strftime("%Y" if by_year else "%Y-%m")
            groups[key].append(value)
        rows = []
        for key, values in sorted(groups.items()):
            growth = Decimal(1)
            for value in values:
                growth *= 1 + value
            label = f"{key}*" if key in (min(groups), max(groups)) else key
            rows.append((label, growth - 1))
    return tuple(rows)


def _drawdown_records(nav: tuple[NAVPoint, ...]) -> tuple[tuple[ReportValue, ...], ...]:
    if not nav:
        return ()
    episodes: list[tuple[ReportValue, ...]] = []
    peak_index = 0
    trough_index = 0
    peak = nav[0].nav
    trough = peak
    active = False
    for index, point in enumerate(nav):
        if point.nav >= peak:
            if active:
                episodes.append(
                    _close_drawdown(nav, episodes, peak_index, trough_index, trough, index)
                )
                active = False
            peak, trough = point.nav, point.nav
            peak_index, trough_index = index, index
            continue
        if not active:
            active = True
            trough, trough_index = point.nav, index
        elif point.nav < trough:
            trough, trough_index = point.nav, index
    if active:
        episodes.append(_close_drawdown(nav, episodes, peak_index, trough_index, trough, None))
    return tuple(episodes)


def _close_drawdown(
    nav: tuple[NAVPoint, ...],
    episodes: list[tuple[ReportValue, ...]],
    peak_index: int,
    trough_index: int,
    trough: Decimal,
    recovery_index: int | None,
) -> tuple[ReportValue, ...]:
    end_index = recovery_index if recovery_index is not None else len(nav) - 1
    peak_value = nav[peak_index].nav
    depth = Decimal(1) - trough / peak_value if peak_value > 0 else None
    duration = Decimal(
        str((nav[end_index].timestamp - nav[peak_index].timestamp).total_seconds())
    ) / Decimal(86400)
    return (
        len(episodes) + 1,
        _iso(nav[peak_index].timestamp),
        _iso(nav[trough_index].timestamp),
        _iso(nav[recovery_index].timestamp) if recovery_index is not None else None,
        "recovered" if recovery_index is not None else "censored",
        depth,
        duration,
        end_index - peak_index,
    )


def _drawdown_episode_chart(
    rows: tuple[tuple[ReportValue, ...], ...],
) -> tuple[tuple[str, Decimal | None], ...]:
    return tuple(
        (f"{row[0]} {row[4]}", row[6] if isinstance(row[6], Decimal) else None) for row in rows
    )


def _histogram(values: list[Decimal]) -> tuple[tuple[str, Decimal], ...]:
    if not values:
        return ()
    low, high = min(values), max(values)
    bins = min(20, max(5, int(len(values) ** 0.5))) if len(values) >= 5 else len(values)
    if low == high:
        return ((str(low), Decimal(len(values))),)
    with localcontext(_context()):
        step = (high - low) / bins
        counts = [0] * bins
        for value in values:
            index = min(int((value - low) / step), bins - 1)
            counts[index] += 1
        return tuple(
            (str(low + (Decimal(index) + Decimal("0.5")) * step), Decimal(count))
            for index, count in enumerate(counts)
        )


def _charts(result: BacktestResult, config: AnalyticsConfig, currency: str) -> tuple[Chart, ...]:
    all_nav = result.nav
    daily = sampled_nav_points(result)
    returns = sampled_returns(result)
    labels = tuple((_iso(point.timestamp) or "", point.nav) for point in all_nav)
    positive_log: list[tuple[str, Decimal | None]] = []
    for point in all_nav:
        if point.nav > 0:
            with localcontext(_context()):
                positive_log.append((_iso(point.timestamp) or "", point.nav.ln()))
        else:
            positive_log.append((_iso(point.timestamp) or "", None))

    peak = daily[0].nav if daily else Decimal(0)
    underwater: list[tuple[str, Decimal | None]] = []
    for point in daily:
        peak = max(peak, point.nav)
        value = Decimal(1) - point.nav / peak if peak > 0 else None
        underwater.append((_iso(point.timestamp) or "", value))

    benchmark_series: tuple[ChartSeries, ...] = ()
    if result.benchmark:
        strategy_scale = daily[0].nav if daily and daily[0].nav > 0 else None
        strategy_points = tuple(
            (_iso(point.timestamp) or "", point.nav / strategy_scale if strategy_scale else None)
            for point in daily
        )
        benchmark_points = tuple(
            (_iso(point.timestamp) or "", point.value) for point in result.benchmark
        )
        benchmark_series = (
            ChartSeries("Strategy normalized NAV", strategy_points),
            ChartSeries("Benchmark price return", benchmark_points),
        )
    benchmark_note = (
        "Benchmark is price-return-only; dividends are excluded and this is not "
        "a total-return comparison."
        if result.benchmark
        else "No benchmark observations are available."
    )

    with localcontext(_context()):
        rf = (Decimal(1) + config.annual_risk_free_rate).ln() / Decimal(config.annualization)
        rf = rf.exp() - 1
        window = 20
        rolling_vol: list[tuple[str, Decimal | None]] = []
        rolling_sharpe: list[tuple[str, Decimal | None]] = []
        values = [value for _, value in returns]
        for end in range(len(values)):
            sample = values[max(0, end - window + 1) : end + 1]
            timestamp = _iso(returns[end][0]) or ""
            if len(sample) < 2:
                rolling_vol.append((timestamp, None))
                rolling_sharpe.append((timestamp, None))
                continue
            mean = sum(sample, Decimal(0)) / Decimal(len(sample))
            variance = sum(((value - mean) ** 2 for value in sample), Decimal(0)) / Decimal(
                len(sample) - 1
            )
            deviation = variance.sqrt()
            rolling_vol.append((timestamp, deviation * Decimal(config.annualization).sqrt()))
            excess_mean = mean - rf
            rolling_sharpe.append(
                (
                    timestamp,
                    Decimal(config.annualization).sqrt() * excess_mean / deviation
                    if deviation > 0
                    else None,
                )
            )

    monthly = _period_returns(returns, False)
    annual = _period_returns(returns, True)
    closed = closed_trades(result)
    excursions = trade_excursions(result)
    pnl_values = [trade.net for trade in closed]
    trade_histogram = _histogram(pnl_values)
    mae_mfe = tuple(
        (str(mae), mfe) for mae, mfe in excursions if mae is not None and mfe is not None
    )

    exposure = tuple((_iso(point.timestamp) or "", point.gross_exposure) for point in daily)
    net_exposure = tuple((_iso(point.timestamp) or "", point.net_exposure) for point in daily)
    gross_lev = tuple((_iso(point.timestamp) or "", point.leverage) for point in daily)
    net_lev = tuple(
        (
            _iso(point.timestamp) or "",
            point.net_exposure / point.nav if point.nav > 0 else None,
        )
        for point in daily
    )
    fills_sorted = sorted(result.fills, key=lambda fill: (fill.observed_at, fill.fill_id.value))
    cumulative_turnover: list[tuple[str, Decimal | None]] = []
    fill_index = 0
    cumulative_notional = Decimal(0)
    positive_nav_total = Decimal(0)
    positive_nav_count = 0
    for point in daily:
        with localcontext(_context()):
            while (
                fill_index < len(fills_sorted)
                and fills_sorted[fill_index].observed_at <= point.timestamp
            ):
                fill = fills_sorted[fill_index]
                cumulative_notional += fill.quantity * fill.price
                fill_index += 1
            if point.nav > 0:
                positive_nav_total += point.nav
                positive_nav_count += 1
            denominator = (
                positive_nav_total / Decimal(positive_nav_count) if positive_nav_count else None
            )
            turnover = cumulative_notional / denominator if denominator else None
        cumulative_turnover.append((_iso(point.timestamp) or "", turnover))

    return (
        Chart("equity", "Equity Curve", "line", "UTC time", "NAV", (ChartSeries("NAV", labels),)),
        Chart(
            "log_equity",
            "Log Equity",
            "line",
            "UTC time",
            "Natural log NAV",
            (ChartSeries("log NAV", tuple(positive_log)),),
            "Nonpositive NAV observations are gaps; log equity is undefined there.",
        ),
        Chart(
            "benchmark",
            "Benchmark Comparison",
            "line",
            "UTC time",
            "Normalized value",
            benchmark_series,
            benchmark_note,
        ),
        Chart(
            "underwater",
            "Underwater Chart",
            "line",
            "UTC session",
            "Drawdown fraction",
            (ChartSeries("Drawdown", tuple(underwater)),),
            "Values below -100% indicate negative NAV.",
        ),
        Chart(
            "drawdown_duration",
            "Drawdown Duration",
            "bar",
            "Peak-to-recovery episode",
            "Elapsed days",
            (ChartSeries("Duration", _drawdown_episode_chart(_drawdown_records(daily))),),
            "Unrecovered episodes are censored at the final sample.",
        ),
        Chart(
            "rolling_volatility",
            "Rolling Volatility",
            "line",
            "UTC session",
            "Annualized volatility",
            (ChartSeries("20-session trailing volatility", tuple(rolling_vol)),),
            f"Trailing 20 observed session returns; minimum 2; "
            f"annualization A={config.annualization}.",
        ),
        Chart(
            "rolling_sharpe",
            "Rolling Sharpe",
            "line",
            "UTC session",
            "Sharpe ratio",
            (ChartSeries("20-session trailing Sharpe", tuple(rolling_sharpe)),),
            f"Trailing 20 observed session returns; minimum 2; A={config.annualization}.",
        ),
        Chart(
            "monthly_returns",
            "Monthly Returns",
            "bar",
            "UTC calendar month",
            "Compounded return",
            (ChartSeries("Monthly return", tuple((key, value) for key, value in monthly)),),
            "Observed returns compound within each period; edge periods may be partial.",
        ),
        Chart(
            "annual_returns",
            "Annual Returns",
            "bar",
            "UTC calendar year",
            "Compounded return",
            (ChartSeries("Annual return", tuple((key, value) for key, value in annual)),),
            "Observed returns compound within each period; edge periods may be partial.",
        ),
        Chart(
            "return_distribution",
            "Return Distribution",
            "bar",
            "Session return bin midpoint",
            "Observations",
            (ChartSeries("Session returns", _histogram([x for _, x in returns])),),
            "Only valid M6 session returns are included.",
        ),
        Chart(
            "trade_pnl_distribution",
            "Trade P&L Distribution",
            "bar",
            f"Net P&L ({currency})",
            "Closed matched trade count",
            (ChartSeries("Closed matched trades", trade_histogram),),
            "FIFO matched trade slices; open residual positions are excluded.",
        ),
        Chart(
            "mae_mfe",
            "MAE / MFE",
            "scatter",
            "MAE per unit",
            "MFE per unit",
            (ChartSeries("Closed matched trade", mae_mfe),),
            "Checkpoint close marks; no intrabar extrema are inferred.",
        ),
        Chart(
            "exposure",
            "Exposure",
            "line",
            "UTC session",
            f"Exposure ({currency})",
            (
                ChartSeries("Gross exposure", exposure),
                ChartSeries("Net exposure", net_exposure),
            ),
        ),
        Chart(
            "leverage",
            "Leverage",
            "line",
            "UTC session",
            "Leverage multiple",
            (
                ChartSeries("Gross leverage", gross_lev),
                ChartSeries("Net leverage", net_lev),
            ),
            "Leverage is null when NAV is nonpositive.",
        ),
        Chart(
            "turnover",
            "Cumulative Turnover",
            "line",
            "UTC session",
            "Traded notional / running mean positive NAV",
            (ChartSeries("Cumulative turnover", tuple(cumulative_turnover)),),
            "Cumulative two-way fill notional divided by the running mean sampled positive NAV.",
        ),
    )


def _chart_data_table(charts: tuple[Chart, ...]) -> ReportTable:
    rows = []
    for chart in charts:
        for series in chart.series:
            for index, (x_value, y_value) in enumerate(series.points):
                rows.append((chart.name, series.name, index, x_value, y_value, chart.note))
    return _table(
        "chart_data",
        ("chart", "series", "point_order", "x_value", "y_value", "note"),
        rows,
    )


def build_report(result: BacktestResult, config: AnalyticsConfig | None = None) -> BacktestReport:
    """Build the complete deterministic report model for one finished backtest."""
    if not isinstance(result, BacktestResult):
        raise DomainError("BacktestResult is required")
    config = config or AnalyticsConfig()
    with localcontext(_context()):
        analytics = analyze(result, config)
        charts = _charts(result, config, analytics.currency)
        tables = _table_rows(result, analytics) + (
            _table(
                "session_returns",
                ("timestamp", "return"),
                [(_iso(timestamp), value) for timestamp, value in sampled_returns(result)],
            ),
            _chart_data_table(charts),
        )
        return BacktestReport(
            "1.0",
            result.strategy_name,
            analytics.currency,
            result.start,
            result.end,
            _safe_metadata(result.metadata),
            config,
            analytics.assumptions,
            analytics.metrics,
            tables,
            charts,
        )
