"""Independent Decimal-based performance, trade and risk analytics."""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext

from quant_terminal.backtesting.models import BacktestResult, NAVPoint
from quant_terminal.core.exceptions import DomainError
from quant_terminal.core.identifiers import InstrumentId
from quant_terminal.orders.models import Side


def _ctx() -> Context:
    return Context(prec=34, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True, slots=True)
class Metric:
    value: Decimal | None
    unit: str
    observations: int
    status: str = "available"
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RiskOfRuinConfig:
    allocation_fraction: Decimal
    ruin_equity_fraction: Decimal
    horizon_trades: int
    paths: int
    seed: int

    def __post_init__(self) -> None:
        for name, value in (
            ("allocation_fraction", self.allocation_fraction),
            ("ruin_equity_fraction", self.ruin_equity_fraction),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or not 0 < value <= 1:
                raise DomainError(f"{name} must be a Decimal in (0, 1]")
        if type(self.horizon_trades) is not int or self.horizon_trades <= 0:
            raise DomainError("horizon_trades must be a positive integer")
        if type(self.paths) is not int or self.paths <= 0:
            raise DomainError("paths must be a positive integer")
        if type(self.seed) is not int:
            raise DomainError("seed must be an integer")


@dataclass(frozen=True, slots=True)
class AnalyticsConfig:
    annualization: int = 252
    annual_risk_free_rate: Decimal = Decimal(0)
    annual_mar: Decimal = Decimal(0)
    var_confidences: tuple[Decimal, ...] = (Decimal("0.95"), Decimal("0.99"), Decimal("0.999"))
    risk_of_ruin: RiskOfRuinConfig | None = None

    def __post_init__(self) -> None:
        if type(self.annualization) is not int or self.annualization <= 0:
            raise DomainError("annualization must be a positive integer")
        for name, value in (
            ("annual_risk_free_rate", self.annual_risk_free_rate),
            ("annual_mar", self.annual_mar),
        ):
            if not isinstance(value, Decimal) or not value.is_finite() or value <= -1:
                raise DomainError(f"{name} must be finite and greater than -1")
        if not self.var_confidences or any(
            not isinstance(x, Decimal) or not x.is_finite() or not 0 < x < 1
            for x in self.var_confidences
        ):
            raise DomainError("VaR confidences must be Decimals in (0, 1)")


@dataclass(frozen=True, slots=True)
class AnalyticsReport:
    strategy_name: str
    currency: str
    metrics: tuple[tuple[str, Metric], ...]
    assumptions: tuple[tuple[str, str], ...]

    def metric(self, name: str) -> Metric:
        try:
            return dict(self.metrics)[name]
        except KeyError as exc:
            raise DomainError(f"Unknown metric: {name}") from exc


@dataclass(slots=True)
class _Lot:
    instrument_id: InstrumentId
    quantity: Decimal
    price: Decimal
    fee: Decimal
    timestamp: datetime
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    instrument_id: InstrumentId
    quantity: Decimal
    net: Decimal
    gross: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    entry_notional: Decimal
    holding_days: Decimal
    entry_price: Decimal
    exit_price: Decimal
    direction: int
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_observed_at: datetime
    exit_observed_at: datetime

    @property
    def return_fraction(self) -> Decimal:
        with localcontext(_ctx()):
            return self.net / self.entry_notional


def _m(
    value: Decimal | None, unit: str, n: int, reason: str | None = None, status: str | None = None
) -> Metric:
    actual_status = status or ("available" if value is not None else "unavailable")
    actual_reason = reason if value is None or status is not None else None
    return Metric(value, unit, n, actual_status, actual_reason)


def _mean(xs: list[Decimal]) -> Decimal | None:
    if not xs:
        return None
    with localcontext(_ctx()):
        return sum(xs, Decimal(0)) / Decimal(len(xs))


def _std(xs: list[Decimal]) -> Decimal | None:
    if len(xs) < 2:
        return None
    with localcontext(_ctx()):
        mu = sum(xs, Decimal(0)) / Decimal(len(xs))
        return (sum(((x - mu) ** 2 for x in xs), Decimal(0)) / Decimal(len(xs) - 1)).sqrt()


def _quantile(xs: list[Decimal], p: Decimal) -> Decimal | None:
    if not xs:
        return None
    ys = sorted(xs)
    with localcontext(_ctx()):
        pos = p * Decimal(len(ys) - 1)
        lo = int(pos)
        hi = min(lo + 1, len(ys) - 1)
        return ys[lo] + (ys[hi] - ys[lo]) * (pos - Decimal(lo))


def sampled_nav_points(result: BacktestResult) -> tuple[NAVPoint, ...]:
    """Return the initial NAV plus the final observed checkpoint for each UTC date."""
    if not result.nav:
        return ()
    last: dict[date, NAVPoint] = {}
    for point in result.nav[1:]:
        if point.timestamp > result.start:
            last[point.timestamp.date()] = point
    return (result.nav[0], *(last[d] for d in sorted(last)))


def sampled_returns(result: BacktestResult) -> tuple[tuple[datetime, Decimal], ...]:
    """Return the valid M6 session returns, dated at their ending checkpoints."""
    points = sampled_nav_points(result)
    out: list[tuple[datetime, Decimal]] = []
    solvent = True
    for prev, cur in zip(points, points[1:], strict=False):
        if prev.nav <= 0 or cur.nav <= 0:
            solvent = False
        if solvent:
            with localcontext(_ctx()):
                out.append((cur.timestamp, cur.nav / prev.nav - 1))
    return tuple(out)


def _returns(points: tuple[NAVPoint, ...]) -> list[Decimal]:
    out: list[Decimal] = []
    solvent = True
    for prev, cur in zip(points, points[1:], strict=False):
        if prev.nav <= 0 or cur.nav <= 0:
            solvent = False
        if solvent:
            with localcontext(_ctx()):
                out.append(cur.nav / prev.nav - 1)
    return out


def closed_trades(result: BacktestResult) -> tuple[ClosedTrade, ...]:
    """Return FIFO-matched closed fill slices with fees allocated as in analytics."""
    if not isinstance(result, BacktestResult):
        raise DomainError("BacktestResult is required")
    lots: dict[InstrumentId, list[_Lot]] = defaultdict(list)
    trades: list[ClosedTrade] = []
    fills = [f for _, f in sorted(enumerate(result.fills), key=lambda x: (x[1].observed_at, x[0]))]
    for fill in fills:
        sign = 1 if fill.side is Side.BUY else -1
        book = lots[fill.instrument.instrument_id]
        opposing_qty = sum(
            (lot.quantity.copy_abs() for lot in book if lot.quantity * sign < 0), Decimal(0)
        )
        closing_qty = min(fill.quantity, opposing_qty)
        with localcontext(_ctx()):
            close_fee_left = (
                fill.commission * closing_qty / fill.quantity
                if closing_qty < fill.quantity
                else fill.commission
            )
            close_qty_left = closing_qty
        remaining = fill.quantity
        while remaining > 0 and book and book[0].quantity * sign < 0:
            lot = book[0]
            matched = min(remaining, lot.quantity.copy_abs())
            with localcontext(_ctx()):
                entry_fee = (
                    lot.fee
                    if matched == lot.quantity.copy_abs()
                    else lot.fee * matched / lot.quantity.copy_abs()
                )
                exit_fee = (
                    close_fee_left
                    if matched == close_qty_left
                    else close_fee_left * matched / close_qty_left
                )
                gross = (fill.price - lot.price) * matched
                if lot.quantity < 0:
                    gross = -gross
                net = gross - entry_fee - exit_fee
                days = Decimal(str((fill.timestamp - lot.timestamp).total_seconds())) / Decimal(
                    86400
                )
            trades.append(
                ClosedTrade(
                    fill.instrument.instrument_id,
                    matched,
                    net,
                    gross,
                    entry_fee,
                    exit_fee,
                    lot.price * matched,
                    days,
                    lot.price,
                    fill.price,
                    1 if lot.quantity > 0 else -1,
                    lot.timestamp,
                    fill.timestamp,
                    lot.observed_at,
                    fill.observed_at,
                )
            )
            lot.quantity += Decimal(sign) * matched
            lot.fee -= entry_fee
            remaining -= matched
            close_fee_left -= exit_fee
            close_qty_left -= matched
            if lot.quantity == 0:
                book.pop(0)
        if remaining > 0:
            with localcontext(_ctx()):
                open_fee = fill.commission - (fill.commission * closing_qty / fill.quantity)
            book.append(
                _Lot(
                    fill.instrument.instrument_id,
                    Decimal(sign) * remaining,
                    fill.price,
                    open_fee,
                    fill.timestamp,
                    fill.observed_at,
                )
            )
    return tuple(trades)


def trade_excursions(
    result: BacktestResult,
) -> tuple[tuple[Decimal | None, Decimal | None], ...]:
    """Return per-closed-trade (MAE, MFE) from observed NAV checkpoint close marks."""
    if not isinstance(result, BacktestResult):
        raise DomainError("BacktestResult is required")
    return _trade_excursion_values(closed_trades(result), result.nav)


def _trade_excursion_values(
    trades: tuple[ClosedTrade, ...], points: tuple[NAVPoint, ...]
) -> tuple[tuple[Decimal | None, Decimal | None], ...]:
    rows: list[tuple[Decimal | None, Decimal | None]] = []
    for trade in trades:
        marks = [
            pos.mark_price
            for point in points
            if trade.entry_observed_at <= point.timestamp <= trade.exit_observed_at
            for pos in point.positions
            if pos.instrument_id == trade.instrument_id
        ]
        if not marks:
            rows.append((None, None))
            continue
        with localcontext(_ctx()):
            excursions = [Decimal(0)]
            excursions.extend(
                Decimal(trade.direction) * (price - trade.entry_price) for price in marks
            )
        rows.append((min(excursions), max(excursions)))
    return tuple(rows)


def _excursions(
    trades: tuple[ClosedTrade, ...], points: tuple[NAVPoint, ...]
) -> tuple[list[Decimal], list[Decimal]]:
    mae_mfe = _trade_excursion_values(trades, points)
    return (
        [mae for mae, _ in mae_mfe if mae is not None],
        [mfe for _, mfe in mae_mfe if mfe is not None],
    )


def _drawdowns(points: tuple[NAVPoint, ...]) -> tuple[Decimal, Decimal, int, Decimal | None]:
    if not points:
        return Decimal(0), Decimal(0), 0, None
    peak = points[0].nav
    peak_i = 0
    active: int | None = None
    max_dd = Decimal(0)
    max_duration = Decimal(0)
    max_sessions = 0
    recovery: Decimal | None = None
    for i, p in enumerate(points):
        if p.nav >= peak:
            if active is not None:
                days = Decimal(
                    str((p.timestamp - points[active].timestamp).total_seconds())
                ) / Decimal(86400)
                max_duration = max(max_duration, days)
                max_sessions = max(max_sessions, i - active)
                recovered = Decimal(
                    str((p.timestamp - points[peak_i].timestamp).total_seconds())
                ) / Decimal(86400)
                recovery = recovered if recovery is None else max(recovery, recovered)
                active = None
            peak, peak_i = p.nav, i
            continue
        if active is None:
            active = peak_i
        if peak > 0:
            with localcontext(_ctx()):
                max_dd = max(max_dd, Decimal(1) - p.nav / peak)
    if active is not None:
        max_duration = max(
            max_duration,
            Decimal(str((points[-1].timestamp - points[active].timestamp).total_seconds()))
            / Decimal(86400),
        )
        max_sessions = max(max_sessions, len(points) - 1 - active)
    return max_dd, max_duration, max_sessions, recovery


def _es(losses: list[Decimal], confidence: Decimal) -> Decimal | None:
    if not losses:
        return None
    ordered = sorted(losses, reverse=True)
    mass = (1 - confidence) * Decimal(len(ordered))
    whole = int(mass)
    fraction = mass - Decimal(whole)
    if whole == 0:
        return ordered[0]
    total = sum(ordered[:whole], Decimal(0))
    if fraction and whole < len(ordered):
        total += ordered[whole] * fraction
    with localcontext(_ctx()):
        return total / mass


def _ruin(trades: tuple[ClosedTrade, ...], cfg: RiskOfRuinConfig | None) -> Metric:
    if cfg is None:
        return _m(
            None, "probability", len(trades), "explicit risk-of-ruin assumptions were not supplied"
        )
    if not trades:
        return _m(None, "probability", 0, "no closed matched trades")
    rng = random.Random(cfg.seed)
    outcomes = [t.return_fraction for t in trades]
    ruined = 0
    with localcontext(_ctx()):
        for _ in range(cfg.paths):
            equity = Decimal(1)
            for _ in range(cfg.horizon_trades):
                equity *= 1 + cfg.allocation_fraction * outcomes[rng.randrange(len(outcomes))]
                if equity <= cfg.ruin_equity_fraction:
                    ruined += 1
                    break
    return _m(Decimal(ruined) / Decimal(cfg.paths), "probability", cfg.paths)


def _analyze(result: BacktestResult, config: AnalyticsConfig | None = None) -> AnalyticsReport:
    """Calculate independent analytics from a completed backtest result."""
    if not isinstance(result, BacktestResult):
        raise DomainError("BacktestResult is required")
    config = config or AnalyticsConfig()
    if not isinstance(config, AnalyticsConfig):
        raise DomainError("AnalyticsConfig is required")
    currency = dict(result.metadata).get("currency", "")
    points = sampled_nav_points(result)
    returns = [value for _, value in sampled_returns(result)]
    trades = closed_trades(result)
    metrics: dict[str, Metric] = {}
    n = len(returns)
    initial = points[0].nav if points else Decimal(0)
    ending = points[-1].nav if points else Decimal(0)
    days = Decimal(str((result.end - result.start).total_seconds())) / Decimal(86400)
    insolvent = any(point.nav <= 0 for point in points)
    total = ending / initial - 1 if initial > 0 else None
    metrics["total_return"] = _m(total, "fraction", n, "initial NAV must be positive")
    cagr = None
    if initial > 0 and ending > 0 and days > 0 and not insolvent:
        with localcontext(_ctx()):
            cagr = ((ending / initial).ln() * Decimal("365.25") / days).exp() - 1
    metrics["cagr"] = _m(
        cagr,
        "fraction_per_year",
        n,
        "positive wealth, positive duration, and no insolvency are required",
    )
    mu = _mean(returns)
    metrics["annualized_arithmetic_return"] = _m(
        mu * config.annualization if mu is not None else None,
        "fraction_per_year",
        n,
        "no valid daily returns",
    )
    sd = _std(returns)
    ann_sd = sd * Decimal(config.annualization).sqrt() if sd is not None else None
    metrics["annualized_volatility"] = _m(
        ann_sd, "fraction_per_year", n, "at least two returns are required"
    )
    with localcontext(_ctx()):
        rf = (Decimal(1) + config.annual_risk_free_rate).ln().__truediv__(
            Decimal(config.annualization)
        ).exp() - 1
        mar = (Decimal(1) + config.annual_mar).ln().__truediv__(
            Decimal(config.annualization)
        ).exp() - 1
        er = [x - rf for x in returns]
        em = [x - mar for x in returns]
        esd = _std(er)
        down = (
            (sum((min(x, Decimal(0)) ** 2 for x in em), Decimal(0)) / Decimal(len(em))).sqrt()
            if em
            else None
        )
        mean_er = _mean(er)
        mean_em = _mean(em)
        sharpe = (
            Decimal(config.annualization).sqrt() * mean_er / esd
            if len(er) >= 2 and esd is not None and esd > 0 and mean_er is not None
            else None
        )
        sortino = (
            Decimal(config.annualization).sqrt() * mean_em / down
            if em and down is not None and down > 0 and mean_em is not None
            else None
        )
    metrics["sharpe"] = _m(
        sharpe, "ratio", n, "requires two returns and positive excess-return deviation"
    )
    metrics["sortino"] = _m(
        sortino, "ratio", n, "downside deviation is zero or returns are missing"
    )
    max_dd, dd_days, dd_sessions, recovery_days = _drawdowns(points)
    metrics["maximum_drawdown"] = _m(
        max_dd if points else None, "fraction", len(points), "no NAV samples"
    )
    metrics["drawdown_duration_days"] = _m(dd_days, "days", len(points))
    metrics["drawdown_duration_sessions"] = _m(Decimal(dd_sessions), "sessions", len(points))
    metrics["recovery_time_days"] = _m(
        recovery_days, "days", len(points), "no drawdown recovered in the sample"
    )
    metrics["calmar"] = _m(
        cagr / max_dd if cagr is not None and max_dd > 0 else None,
        "ratio",
        n,
        "requires defined CAGR and positive drawdown",
    )
    if points:
        peaks = []
        peak = points[0].nav
        for p in points:
            peak = max(peak, p.nav)
            peaks.append(peak)
        dds = [Decimal(1) - p.nav / pk for p, pk in zip(points, peaks, strict=True) if pk > 0]
        with localcontext(_ctx()):
            ulcer = (
                (sum((x * x for x in dds), Decimal(0)) / Decimal(len(dds))).sqrt() if dds else None
            )
    else:
        ulcer = None
    metrics["ulcer_index"] = _m(ulcer, "fraction", len(points), "no positive running peak")

    pnls = [t.net for t in trades]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x < 0]
    tc = len(trades)
    metrics["trade_count"] = _m(Decimal(tc), "trades", tc)
    metrics["win_rate"] = _m(
        Decimal(len(wins)) / tc if tc else None, "fraction", tc, "no closed matched trades"
    )
    metrics["loss_rate"] = _m(
        Decimal(len(losses)) / tc if tc else None, "fraction", tc, "no closed matched trades"
    )
    metrics["breakeven_trades"] = _m(Decimal(sum(x == 0 for x in pnls)), "trades", tc)
    profit_factor = sum(wins, Decimal(0)) / abs(sum(losses, Decimal(0))) if losses else None
    metrics["profit_factor"] = _m(
        profit_factor,
        "ratio",
        tc,
        "no losing trades" if not losses else None,
    )
    metrics["expectancy"] = _m(_mean(pnls), currency, tc, "no closed matched trades")
    aw, al = _mean(wins), _mean(losses)
    metrics["average_win"] = _m(aw, currency, len(wins), "no winning trades")
    metrics["average_loss"] = _m(al, currency, len(losses), "no losing trades")
    payoff_ratio = aw / abs(al) if aw is not None and al is not None and al != 0 else None
    metrics["payoff_ratio"] = _m(
        payoff_ratio,
        "ratio",
        tc,
        "requires nonzero mean losses and winning trades" if payoff_ratio is None else None,
    )
    metrics["median_trade"] = _m(
        _quantile(pnls, Decimal("0.5")), currency, tc, "no closed matched trades"
    )
    metrics["best_trade"] = _m(
        max(pnls) if pnls else None, currency, tc, "no closed matched trades"
    )
    metrics["worst_trade"] = _m(
        min(pnls) if pnls else None, currency, tc, "no closed matched trades"
    )
    metrics["average_holding_days"] = _m(
        _mean([t.holding_days for t in trades]), "days", tc, "no closed matched trades"
    )
    runs = [(t.net > 0, t.net < 0) for t in trades]
    max_wins = max_losses = run_wins = run_losses = 0
    for win, loss in runs:
        run_wins, run_losses = (run_wins + 1 if win else 0), (run_losses + 1 if loss else 0)
        max_wins, max_losses = max(max_wins, run_wins), max(max_losses, run_losses)
    metrics["max_consecutive_wins"] = _m(Decimal(max_wins), "trades", tc)
    metrics["max_consecutive_losses"] = _m(Decimal(max_losses), "trades", tc)
    maes, mfes = _excursions(trades, result.nav)
    metrics["average_mae_per_unit"] = _m(_mean(maes), currency, len(maes), "no closed trade marks")
    metrics["average_mfe_per_unit"] = _m(_mean(mfes), currency, len(mfes), "no closed trade marks")

    gross = [p.gross_exposure for p in points]
    net = [p.net_exposure for p in points]
    gross_lev = [p.leverage for p in points if p.leverage is not None]
    net_lev = [p.net_exposure / p.nav for p in points if p.nav > 0]
    metrics["average_gross_exposure"] = _m(_mean(gross), currency, len(gross), "no NAV samples")
    metrics["average_net_exposure"] = _m(_mean(net), currency, len(net), "no NAV samples")
    metrics["average_gross_leverage"] = _m(
        _mean(gross_lev), "multiple", len(gross_lev), "NAV must be positive"
    )
    metrics["average_net_leverage"] = _m(
        _mean(net_lev), "multiple", len(net_lev), "NAV must be positive"
    )
    positive_nav = [p.nav for p in points if p.nav > 0]
    traded = sum((f.quantity * f.price for f in result.fills), Decimal(0))
    avg_nav = _mean(positive_nav)
    metrics["turnover"] = _m(
        traded / avg_nav if avg_nav else None,
        "multiple",
        len(result.fills),
        "mean positive NAV is unavailable",
    )
    metrics["commissions"] = _m(result.commission_cost, currency, len(result.fills))
    metrics["slippage_cost"] = _m(result.slippage_cost, currency, len(result.fills))
    metrics["total_cost"] = _m(result.total_cost, currency, len(result.fills))

    if len(returns) >= 3 and mu is not None:
        with localcontext(_ctx()):
            centered = [x - mu for x in returns]
            m2 = sum((x * x for x in centered), Decimal(0)) / Decimal(len(returns))
            skew = (
                sum((x**3 for x in centered), Decimal(0)) / Decimal(len(returns)) / (m2 * m2.sqrt())
                if m2
                else None
            )
    else:
        skew = None
    metrics["skew"] = _m(skew, "ratio", n, "requires three observations and nonzero variance")
    if len(returns) >= 4 and mu is not None:
        with localcontext(_ctx()):
            centered = [x - mu for x in returns]
            m2 = sum((x * x for x in centered), Decimal(0)) / Decimal(len(returns))
            kurt = (
                sum((x**4 for x in centered), Decimal(0)) / Decimal(len(returns)) / (m2 * m2) - 3
                if m2
                else None
            )
    else:
        kurt = None
    metrics["excess_kurtosis"] = _m(
        kurt, "ratio", n, "requires four observations and nonzero variance"
    )
    p95, p05 = _quantile(returns, Decimal("0.95")), _quantile(returns, Decimal("0.05"))
    metrics["tail_ratio_95_5"] = _m(
        p95 / abs(p05) if p95 is not None and p05 else None,
        "ratio",
        n,
        "5th percentile is zero or missing",
    )
    with localcontext(_ctx()):
        up = sum((max(x - mar, Decimal(0)) for x in returns), Decimal(0))
        down = sum((max(mar - x, Decimal(0)) for x in returns), Decimal(0))
        omega = up / down if down else None
    metrics["omega"] = _m(omega, "ratio", n, "no shortfall below MAR")
    for confidence in config.var_confidences:
        label = format((confidence * 100).normalize(), "f")
        tail_n = Decimal(n) * (1 - confidence)
        inadequate = n < 20 or tail_n < 5
        status = "insufficient_sample" if inadequate else None
        reason = (
            "requires at least 20 returns and 5 expected tail observations" if inadequate else None
        )
        losses_r = [-x for x in returns]
        metrics[f"var_{label}"] = _m(
            _quantile(losses_r, confidence), "loss_fraction", n, reason, status
        )
        metrics[f"expected_shortfall_{label}"] = _m(
            _es(losses_r, confidence), "loss_fraction", n, reason, status
        )
    metrics["risk_of_ruin"] = _ruin(trades, config.risk_of_ruin)
    for name in (
        "alpha",
        "beta",
        "tracking_error",
        "information_ratio",
        "up_capture",
        "down_capture",
    ):
        metrics[name] = _m(
            None,
            "ratio",
            0,
            "M5 benchmark is price-only and lacks a compatible total-return session series",
        )

    assumptions = (
        ("annualization", str(config.annualization)),
        ("annual_mar", str(config.annual_mar)),
        ("annual_risk_free_rate", str(config.annual_risk_free_rate)),
        ("sample_period", "last checkpoint per UTC calendar date"),
        ("trade_matching", "FIFO matched fill slices; open residuals excluded"),
        ("insolvent_path", str(insolvent)),
    )
    return AnalyticsReport(
        result.strategy_name, currency, tuple(sorted(metrics.items())), assumptions
    )


def analyze(result: BacktestResult, config: AnalyticsConfig | None = None) -> AnalyticsReport:
    """Calculate metrics under a fixed Decimal precision and rounding policy."""
    with localcontext(_ctx()):
        return _analyze(result, config)
