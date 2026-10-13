# M7 complete backtest report contract

## Report boundary and tables

`build_report` consumes one immutable `BacktestResult` and an optional
`AnalyticsConfig`. It calculates the `AnalyticsReport` and public closed FIFO
trade slices from that same result through M6, without re-running the strategy
or changing analytic values. The report snapshot contains report version,
strategy, start/end, currency, declared assumptions, typed metrics, and
normalized tables for session NAV, closed matched trades, open positions,
orders with lifecycle events, fills, benchmark observations, and exposure.
Order-level `Trade` objects are executions per order and are never presented as
round-trip trades. Closed matched slices use the M6 FIFO fee-allocation
convention. Unclosed exposure is disclosed separately.

The portable bundle contains `report.html`, `report.json`, `bundle.json`, and
`tables/{nav,session_returns,chart_data,metrics,risk_metrics,trading_statistics,exposure_metrics,drawdowns,execution_costs,trades,positions,orders,fills,benchmark,exposure}.{csv,parquet}`.
JSON serializes Decimal values as strings and UTC timestamps as ISO-8601.
CSV uses stable headers, UTC timestamps, decimal strings, and empty cells for
nulls. String cells beginning (after whitespace) with `=`, `+`, `-`, `@`, tab,
or carriage return receive a leading apostrophe to prevent spreadsheet formula
execution. Parquet stores exact decimal strings and timestamp fields as UTC; table
schema/version, row count, byte count, and SHA-256 are recorded in `bundle.json`.
The bundle manifest hashes every other artifact but does not self-hash. Output
is staged beside its destination and atomically finalized; an existing bundle
destination is rejected to avoid mixed revisions.

## Report sections

Summary, Performance, Risk, Trading Statistics, Drawdowns, Execution Costs,
Exposure, Benchmark Comparison, Trades, Orders, and Fills are rendered from
the typed report snapshot. Missing metrics retain their status and reason.
Order rows retain current terminal state, accepted time, requested/filled/
remaining quantities, rejection reason, and ordered lifecycle events. Fill
rows include both economic and observed UTC time, reference and executed price,
commission, side, quantity, instrument, and order/fill identifiers.

## Time series and chart conventions

Daily metrics and period returns use the M6 convention: the initial NAV plus the
last observed checkpoint on each UTC date; missing dates are not filled. The
equity curve uses all observed checkpoints. Log equity is defined only for
positive NAV; nonpositive points are represented as gaps and accompanied by an
insolvency notice. Underwater values are `1 - NAV/running_peak`, including
values below -100% for negative NAV. Drawdown episodes expose peak, trough,
recovery (or censored end), depth, and peak-to-recovery/censored elapsed time and
observed checkpoint count; this interval convention matches M6.

Rolling volatility and Sharpe use trailing 20 observed session returns with at
least 2 observations and configured annualization `A` (default 252); the window
and annual risk-free assumption are shown. Monthly and annual returns compound the available session returns within
UTC calendar periods, mark first and last observed periods with an asterisk as
boundary-partial periods, and never invent missing months.
Returns following insolvency are omitted as in M6. Return and net closed-trade
P&L distributions use deterministic equal-width histogram bins (between 5 and
20 bins when there are at least 5 observations); empty distributions display an
explicit unavailable state. MAE/MFE charts use M6 matched-trade results and
checkpoint-close marks, with no claimed intrabar extrema. Exposure and leverage
charts use the sampled daily NAV; nonpositive-NAV leverage stays null. Turnover
is displayed as cumulative traded notional divided by the running mean of
positive sampled NAV, with the M6 two-way traded-notional definition.

Benchmark observations are normalized price returns, excluding dividends. The
chart is labeled price-return-only and never implies total-return comparison;
alpha, beta, tracking error, information ratio, and capture metrics remain
unavailable. Dates/currencies are not interpolated or silently aligned across
gaps.

## HTML safety and determinism

HTML is a self-contained offline document with inline CSS and locally generated
SVG charts; it requests no remote scripts, fonts, images, or data. User-derived
labels and text are HTML-escaped. Decimal and timestamp output is locale
independent. Charts are static and printable. Sensitive metadata keys matching
secret, token, password, credential, API-key, private-key, access-key,
signing-key, or auth-key terms are redacted before any export; punctuation and
spacing variations are normalized before matching.

PDF is deferred. This is a research report, not a trade approval or execution
instruction.
