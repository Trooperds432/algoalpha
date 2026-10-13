# M6 performance and risk analytics contract

## Input and sampling

Analytics consumes only an immutable `BacktestResult` and explicit analytics
assumptions. It does not inspect source data or rerun strategies. The portfolio
sample is the initial NAV plus the final checkpoint on each UTC calendar date
with observations (the supported XNYS closes fall on that date). Missing dates
are not forward-filled. Daily simple returns are `V[t]/V[t-1]-1` when both NAVs
are positive; returns after insolvency or with nonpositive endpoints are omitted.
Once insolvency occurs, later returns are omitted rather than joining disconnected
positive-equity periods. The report flags any nonpositive NAV sample.
The default annualization is `A=252` sessions and can be set explicitly in
`AnalyticsConfig`; elapsed-time CAGR uses 365.25 days.
Risk-free rate and MAR are annual rates converted to periodic rates by
`(1+r_annual)^(1/A)-1`. The defaults are explicitly zero.

## Return and risk formulas

- Total return: `V[last]/V[initial]-1`.
- CAGR: `(V[last]/V[initial])^(365.25/elapsed_days)-1`; null for nonpositive
  wealth, nonpositive duration, or any nonpositive sampled NAV.
- Annualized arithmetic return: `A * arithmetic_mean(daily returns)`.
- Annualized volatility: `sqrt(A) * sample_std(daily returns)`, `ddof=1`.
- Sharpe: `sqrt(A) * mean(r-rf_period) / sample_std(r-rf_period)`.
- Sortino: `sqrt(A) * mean(r-MAR_period) / sqrt(mean(min(r-MAR_period,0)^2))`.
  The downside denominator includes all sampled periods.
- Maximum drawdown: maximum `1 - V/running_peak(V)`, including the initial NAV.
  Drawdown can exceed 100% when NAV is negative and is flagged as insolvent.
- Drawdown duration: longest peak-to-recovery interval; unrecovered episodes are
  censored at the final sample. Report elapsed days and observed-session counts.
- Recovery time: longest completed peak-to-recovery interval; null when no
  drawdown recovered in the sample.
- Calmar: CAGR divided by positive maximum drawdown; otherwise null.
- Ulcer Index: square root of the mean squared sampled percentage drawdown.

Sharpe, Sortino, and volatility require at least two valid daily returns. Ratios
with zero denominators are null with a reason; infinity is never emitted.

## Closed FIFO trade statistics

Fills are replayed by observation time in stable result order and matched using
FIFO lots, separately by instrument and direction. The public
`closed_trades()` function exposes the same immutable closed matched slices
used by the metrics, with gross and net P&L, allocated entry/exit fees,
quantity, prices, and economic/observed timestamps. A closed matched lot slice
is one trade; open residual lots are not wins or losses. Gross P&L for a long is
`(exit-entry)*quantity`; for a short it is `(entry-exit)*quantity`. Entry and
exit commissions are allocated pro rata to matched quantity, with a final
residual assigned to the last closed slice. Net P&L subtracts both fees.

Win/loss rates use closed matched trade count; breakeven is separate. Profit
factor is winning net P&L divided by absolute losing net P&L. Expectancy is mean
net P&L per matched trade. Payoff ratio is mean win divided by absolute mean
loss. Average win/loss, median/best/worst trade, maximum consecutive wins/losses,
trade count, and average holding days use closed matched slices. No-loss profit
factor and zero-denominator ratios are null with reasons.

MAE/MFE use every available `BacktestResult` NAV checkpoint's instrument mark
from entry-fill observation through exit-fill observation. For each closed matched trade they report maximum
adverse/favorable price excursion per unit relative to entry, respecting long
or short direction. These checkpoint marks are close-price samples and do not
bound or reconstruct intrabar high/low excursions. Aggregate metrics are
arithmetic means over closed matched trades with marks. The entry baseline is
included, so MAE is never positive and MFE is never negative. No closed trades
with marks yields unavailable metrics.

## Portfolio, costs, and exposure

Average gross/net exposure and gross/net leverage are arithmetic means over
session samples; leverage samples with nonpositive NAV are excluded and flagged.
Turnover is `sum(abs(fill quantity * fill price)) / mean(positive sampled NAV)`
using the two-way traded-notional convention. Commission and signed slippage
costs are reported from the result ledger in its currency; signed slippage may be
negative when execution improves on the reference price.

## Secondary distribution and tail metrics

Omega is the sum of positive `(r-MAR_period)` divided by the absolute sum of
negative `(r-MAR_period)`. Skew is population central moment 3 divided by
population standard deviation cubed; kurtosis is population central moment 4
divided by variance squared minus 3. Tail ratio is interpolated 95th percentile
return divided by the absolute interpolated 5th percentile return. Consecutive
win/loss metrics are maximum run lengths of closed matched trades in close order.
Breakeven trades break both a win run and a loss run.

Historical VaR is the linear empirical quantile of losses `-r` at 95%, 99%, and
99.9%. Expected Shortfall is the fractional empirical upper-tail mean: average
the largest `(1-confidence)*N` losses, weighting the boundary observation
fractionally. Estimates are returned with `insufficient_sample` status when
there are fewer than 20 returns or fewer than 5 expected tail observations.

Risk of ruin is unavailable unless an explicit assumption supplies allocation
fraction, ruin equity fraction, trade horizon, path count, and integer seed. With
those supplied, resample closed matched trade returns with replacement and
compound `equity *= 1 + allocation_fraction * trade_return`; ruin occurs when
equity is at or below the specified starting-equity fraction. This is a
model-conditional bootstrap probability, not a forecast.

## Benchmark outputs and general null rules

Alpha, beta, tracking error, information ratio, and up/down capture are null
with a reason unless a benchmark is compatible in currency, sessions, and total
return convention. M5 currently emits price-only benchmark values, so these
metrics are explicitly unavailable rather than presented as total-return
comparisons.

Each output is a typed metric with value or null, unit, sample count, status, and
reason. Missing observations, insufficient samples, insolvency, unsupported
benchmark conventions, and zero denominators are explicit. All formulas use
Decimal arithmetic except deterministic index selection for seeded bootstrap.
