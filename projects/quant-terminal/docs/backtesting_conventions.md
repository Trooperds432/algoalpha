# Backtesting conventions, version 0.1 accepted M0 baseline

## Time, calendar and visibility
UTC-aware timestamps internally; source exchange IANA timezone and calendar version
are retained. A bar spans [start,end); available_at is at least end plus declared
publication delay. Daily bars follow actual session closes, including half days/DST.
Strategy on_bar runs only after availability. Future end or availability requests fail.
Orders generated from a completed bar cannot fill in that same bar. At each next bar,
process effective corporate actions, eligible existing orders, fills/accounting and
close valuation before delivering the completed bar to the strategy. Same-time events
use priority: effective corporate actions, eligible existing orders, fill/risk events,
accounting updates, close marks, completed-bar callback, new intents, then DAY expiry
at session end. Ties use instrument ID then submission sequence, stored in the manifest.
Bar-model fills have an economic timestamp (open for market/gap fills; bar end for
intrabar touches whose exact time is unknown) and a separate observed_at equal to
bar availability. Callbacks and the monotonic simulation clock use observed_at, never
rewind to economic time. Full-bar volume is known retrospectively to the simulator,
not to the strategy at the open. New callback intents cannot act before observed_at.
An order submitted inside a bar is ineligible for whole-bar OHLC matching; wait for
the next full bar. Availability delay must never permit retroactive fills.

## Prices, fills and costs
Market fills at next eligible open plus adverse slippage. Buy limit: open improvement
if open <= limit, otherwise touch if low <= limit; inverse for sells. Final adverse
slippage cannot cross the limit: no fill if it would. Buy stop: open if gapped above
stop, otherwise stop on high touch; inverse for sell. Triggered stop-limit becomes
eligible as limit only on the following bar (conservative ambiguity policy). Competing
dependent exits are not supported initially; reject instead of inventing intrabar order.
Model assumptions and ambiguous bar counts are included in reports.

Per-symbol available bar quantity = floor(volume * participation_rate / quantity_step)
* quantity_step, shared across orders by FIFO acceptance sequence. Default research
participation is 1%; explicitly configurable. Zero/missing volume means no fills.
This retrospective liquidity approximation is labeled, not a claim of executable depth.
Risk is rechecked at actual proposed fill prices, with fees and reserved outstanding
commitments; an unaffordable residual is rejected, never silently financed.
DAY expires at session end; GTC persists within run; no after-hours fills by default.
Delayed publication of the final session bar: evaluate existing orders against
economically eligible session prices before applying DAY expiry to the residual.
Deliver both fill and expiry updates at observed_at; new intents from those callbacks
are eligible only for a subsequent session, never the expired session. Add a delayed
close fixture; economic eligibility is separate from observation time.

Commission configuration is required: per-unit and/or notional bps, per-order minimum,
currency rounding, and fee increments per partial fill. Cumulative charged commission
equals max(minimum, cumulative calculated fee) after first fill; later partial fills
charge only the incremental difference. Cancelled unfilled orders pay no commission.
Slippage supports none/fixed/bps/volume-share with explicit parameters; no double
counting spread already included in the model. Zero costs are allowed only with
an explicit ZERO_COST_ASSUMPTION label. Finite nonnegative inputs required.

## Accounting
Single currency per run; reject mismatched instruments. Decimal precision 34,
ROUND_HALF_EVEN ledger arithmetic from decimal strings. Execution prices quantise
adversely to tick (ceil for buys, floor for sells), then recheck positivity, limit
constraints and affordability. For tick 0.01 a raw buy of100.005 becomes100.01, not100.00.
Quantities round down to step before risk validation. Fees round to currency minor
units; fill prices and sizes obey instrument ticks/steps. Internal P&L retains Decimal
precision until export. Presentation rounding never changes ledger values.
Cash decreases by signed quantity * price + fee (buy positive, sell negative).
NAV = cash + dividend receivables + sum(signed quantity * mark). No external cash flows
inside a research run initially. FIFO lot matching; entry/exit fees allocated by matched
quantity, with rounding residual assigned to final lot so totals reconcile.
Signed short accounting is tested; cash policy blocks short exposure unless the manifest
explicitly names a research-only borrow/financing assumption. No production short claim.
At nonpositive NAV halt new risk, record insolvency and leave liquidation assumptions
explicit. Do not invent prices or usable CAGR/log returns after insolvency.

## Corporate actions and marks
Raw prices plus effective-dated split/dividend events are the primary model. Reject
total-return/adjusted prices combined with cash action credits. Split changes units,
cost per unit and pending order price/quantity consistently, preserving value. Dividend
entitlement uses pre-ex-date holding, receivable accrues ex-date and transfers to cash
on pay-date; shorts accrue a liability. Unknown action timing blocks the affected run.
Missing execution bars are not forward-filled. Last mark may carry for valuation with
stale status; stale mark blocks new risk. Final positions remain open and disclosed;
an explicit end-liquidation option must generate priced fills/costs, never erase exposure.

## Analytics
NAV returns include initial capital as baseline: r[t]=V[t]/V[t-1]-1. No cash flows in
initial model. Total return = Vlast/V0-1. CAGR uses actual elapsed days/365.25 and
requires positive starting/ending wealth, positive duration and no insolvency.
Default annual metrics use session-close returns, A=252 explicit; minute runs aggregate
to sessions rather than pretending all minute observations are independent.
Annual risk-free rate defaults explicitly to 0; periodic rf=(1+annual_rf)^(1/A)-1.
Sample volatility uses ddof=1. Sharpe=sqrt(A)*mean(r-rf)/sample_std(r-rf).
Sortino uses sqrt(mean(min(r-MAR,0)^2)) over all periods; MAR convention recorded.
Sortino numerator is sqrt(A)*mean(r-periodic_MAR). An annual MAR converts as
(1+annual_MAR)^(1/A)-1; default annual MAR=0. Denominator is the per-period downside
deviation just defined. Report null when it is zero, not an infinite ratio.
Fewer than two returns, zero denominators and missing samples produce null + reason.
Do not substitute infinity in JSON. Calmar=CAGR/positive maximum drawdown; undefined
when denominator zero. Peak drawdown=1-V/running_max(V), with duration in both elapsed
time and sessions; unrecovered episodes are censored, not counted as recovered.
NAV below zero permits drawdown >100% in signed/leverage fixtures and is flagged.

Historical VaR uses losses=-returns, linear empirical quantile, horizon one session.
ES uses quantile-integral fractional tail weighting, not a threshold mean that mishandles
ties. Tail sample count is reported; estimates with <20 observations or <5 expected
tail observations are flagged insufficient. No Gaussian assumption implied.
Win rate counts closed FIFO matched trades with positive net P&L; breakeven is separate.
Profit factor=sum(positive net P&L)/abs(sum(negative net P&L)), undefined without losses.
Turnover=sum(abs(fill notional))/mean positive sampled NAV (two-way traded-notional
convention). Gross/net leverage=sum(abs/signed market values)/NAV; undefined if NAV<=0.
MAE/MFE use observable post-entry marks, label bar-resolution bounds, no inferred paths.
Remaining M6 metrics require their own formula/edge-case fixtures before implementation.

## Benchmark, randomness and research protocols
Benchmark defaults to none; alpha/beta/information ratio are unavailable until compatible
currency, sessions and total-return convention are supplied. No fabricated benchmark.
Use explicit integer seed, named independent generators and deterministic trial/window
IDs. Same seed does not prove statistical validity. Stable sorting resolves ties.
Walk-forward IS/OOS intervals are half-open, disjoint and frozen. Warm-up uses only past
data and contributes no returns. Initial stitch policy resets positions at each OOS
window, funds it from prior OOS terminal equity and includes transition liquidation
costs using the final eligible OOS close under an explicitly scheduled end-of-window
liquidation model; intervals with no executable liquidation mark fail instead of
using future data. IS portfolio wealth is never stitched into OOS. Other carry policies
need separate manifests and fixtures. Selection cannot access OOS data.
Monte Carlo distinguishes trade/IID/block bootstrap and preserves all simulation
parameters. It quantifies model-conditional uncertainty, not guaranteed future returns.
