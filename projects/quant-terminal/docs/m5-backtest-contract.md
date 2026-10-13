# M5 event-driven backtest contract

## Causal processing order

The runner consumes immutable dataset snapshots in event order. For each bar it
advances the simulation clock to `available_at`, processes only orders accepted
before that bar starts, applies those fills once to the M3 ledger, marks the
bar's instrument to the completed close, records the portfolio checkpoint, and
then calls the strategy. Orders returned by the strategy are validated and
submitted at the current observable time, so they cannot trade on the bar that
caused the signal. A fill's economic time may be the bar start, but no strategy
or portfolio update may observe it before the bar is available.

Strategies receive a narrow context with current observable time, bounded
history access, portfolio snapshot, and active order snapshots. They do not
receive the dataset store or broker. A history request after the context clock
is rejected by `DataFeed`. Each instrument uses one frequency per run, matching
M4 execution capacity semantics.

## Strategy and risk contracts

Strategies implement `on_bar(context, bar) -> iterable[OrderIntent]`. Included
examples are BuyAndHold, MovingAverageCross, and SimpleMomentum, all deterministic
and using only context-visible bars. A pre-trade risk gate rejects intents with
future creation times, unsupported instruments, duplicate IDs, excessive
notional, or sells beyond available long quantity unless explicit research
shorting is enabled. Cash equity buys reserve a conservative notional and fees;
market and stop-market buys require a finite price cap. Every fill is checked
again against its actual cumulative order notional, execution-price cap, and
available cash after other active reservations; unaffordable residuals are rejected.
Zero-cost execution requires the explicit `ZERO_COST_ASSUMPTION` label. This is
a basic execution guard, not the M6 risk library. Custom execution models that
produce zero costs must expose `is_zero_cost = True` so the runner can enforce
the label requirement.

## Results and fixtures

`BacktestResult` contains immutable NAV checkpoints, cash, positions, returns,
orders, fills, terminal execution trades, commission and slippage costs,
exposures, optional benchmark values, and reproducibility metadata. Returns use
consecutive observed checkpoint NAV values; the first checkpoint return is
null. No return is synthesized for missing market events.
Benchmark values are normalized price returns only, exclude dividends, begin at
the first observed close, and carry the last observation forward with an explicit
price-return-only marker.

Golden fixtures compare each event and the final BuyAndHold output by hand,
including order timing, fills, fees, NAV, returns, exposures, and benchmark
alignment. A dedicated strategy attempts to request t+1 history while processing
t; the run must reject the request without leaking future data. Additional
fixtures cover no-trade periods, multiple instruments with aligned timestamps,
invalid strategies, pre-trade and fill-time affordability rejection, explicit
zero-cost labeling, simultaneous cash reservations, and consistency with the M3
ledger.

M5 runs trusted in-process Python strategies only. It is not a hostile-code
sandbox. No broker connection, paper order, or live trading is part of the
backtester.
