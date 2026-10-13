# M4 order and historical execution contract

## Scope and causal timing

This milestone adds an in-process historical broker for equity and ETF bar
backtests. It accepts immutable order intents, maintains an auditable lifecycle,
simulates fills, and emits immutable fill records consumable by the M3 ledger.
It has no network, broker, or paper-trading adapter.

An order is eligible only on a bar whose `start` is at or after its acceptance
time. A bar can be evaluated only once `available_at` is observable to the
simulation clock. Thus an order created in response to a bar cannot fill on
that bar. OHLC does not reveal intrabar event order; assumptions below define
the fill result instead of implying tick precision.

## Order records and lifecycle

`CommissionModel`, `SlippageModel` and `FillModel` are replaceable model
interfaces. The default fee and slippage implementations are parameterized
Decimal models; `OHLCFillModel` supplies the documented bar matching behavior.

Order intent, order-state event, fill and execution trade are separate immutable
records. The order manager retains an append-only lifecycle history; current
status is a snapshot derived from it. A terminal order with one or more fills
produces one execution trade aggregating those fills, including when the order
was partially filled and then cancelled or expired. An unfilled terminal order
has no execution trade. Portfolio round-trip trade matching is a later analytics
concern.

Valid transitions are `CREATED → SUBMITTED → ACCEPTED`, then to
`PARTIALLY_FILLED` or a terminal state. Partial fills may repeat until
`FILLED`; accepted or partially filled orders may become `CANCELLED`, `EXPIRED`
or `REJECTED` only when the simulator explicitly records the corresponding
event. Filled quantity may never exceed order quantity. Duplicate order and
fill identifiers reject without changing state. Cancellation prevents future
fills and does not erase earlier partial fills. Expiry is checked before
matching a bar whose start is at or beyond `expires_at`. Acceptance and expiry
timestamps are timezone-aware and normalized to UTC; expiry must follow
acceptance. Every order quantity is positive and a multiple of instrument
quantity step. Limit, stop and stop-limit prices must be positive and on the
instrument tick; market orders carry no trigger/limit price.

Transition table: `CREATED → SUBMITTED | REJECTED`; `SUBMITTED → ACCEPTED |
REJECTED | CANCELLED`; `ACCEPTED → PARTIALLY_FILLED | FILLED | CANCELLED |
EXPIRED | REJECTED`; `PARTIALLY_FILLED → PARTIALLY_FILLED | FILLED | CANCELLED
| EXPIRED | REJECTED`. `FILLED`, `CANCELLED`, `REJECTED` and `EXPIRED` are
terminal states.

## OHLC matching assumptions

Orders must be active before the bar starts. Matching uses the bar's open/high/
low/volume and occurs only after the complete bar is observable. Market orders
use the open as the reference. A buy limit is eligible when open is at/below
limit or low touches limit; a sell limit is eligible when open is at/above limit
or high touches limit. A marketable limit gets the open as reference; a touched
limit gets its limit price. Buy stops trigger when open is at/above stop or high
touches stop; sell stops mirror this. A gap-through stop uses the open; otherwise
the stop price is the reference. Limit and stop fills have no queue-position
model; a qualifying touch is treated as executable subject to bar-volume
capacity.

Stop-limit orders use the same stop trigger conditions, but a trigger during a
bar only activates the limit for the next eligible bar. OHLC cannot establish
whether the limit price was available after the trigger during the trigger bar,
so same-bar execution is prohibited. The configured limit must be on the
executable side of the stop (buy limit >= stop; sell limit <= stop). A
stop-limit order triggered in one bar first becomes fill-eligible on a later
bar whose start is at or after the trigger bar's end, including across session
gaps. This is a retrospective next-bar-open convention for orders accepted
before the eligible bar starts; an order accepted during a bar cannot fill on it.
Bars for each instrument and frequency must be processed in increasing start
time, and one broker run uses one execution frequency per instrument to avoid
counting overlapping daily and minute-bar volume twice. Stop triggers latch
even when the trigger bar has no executable volume;
fills still require positive shared capacity. Order acceptance, cancellation,
rejection and bar-observation times cannot move backward within one broker run;
orders accepted at the same timestamp are prioritized by OrderId.

Fill quantity is rounded down to the instrument quantity step and capped by
remaining order quantity and the configured participation fraction of bar
volume. Capacity is shared across all orders for the same instrument and bar.
The simulator processes them by acceptance time, then OrderId as a deterministic
tie-breaker; consumed capacity is unavailable to later orders. At most one fill
is emitted for each order on a bar. A zero-volume bar cannot fill. Supported
slippage models are none,
fixed price amount, basis points of reference price, and volume-share impact
proportional to squared participation. Slippage is adverse: added for buys and
subtracted for sells, then rounded against the trader to a valid instrument tick
(buy up, sell down). All order types receive the configured slippage calculation.
Limit and stop-limit orders retain their limit-price protection after the
slippage calculation; the simulated price is clamped so it cannot breach the
limit. A resulting nonpositive execution price is rejected as an invalid model
outcome and rejects the remaining order quantity. Commission is calculated
cumulatively per order: after each fill, target commission is
`max(per_unit_rate × cumulative_filled_quantity, minimum)` when a minimum is
configured, otherwise `per_unit_rate × cumulative_filled_quantity`; this fill's
fee is the target less commission already charged. Fees remain exact Decimal
amounts and are not rounded to currency minor units by this layer.
Commission and slippage assumptions are explicit configuration attached to the
simulation, not inferred from market prices.

The simulator emits M3 `LedgerFill`-compatible facts. Its caller applies them
to the portfolio ledger; the simulator does not maintain a second cash or
position book. `Fill.timestamp` is the economic bar-start timestamp, while
`Fill.observed_at` records when the completed bar made the fill knowable. The
caller must apply the ledger fill only at or after `observed_at`; M3's ledger
record has no separate observation timestamp. Rejected/cancelled/unfilled
quantity incurs no commission.

## Golden tests and known limits

Fixtures cover market orders, limit touches and gaps, stop triggers and gaps,
stop-limit next-bar behavior, buy/sell slippage direction and adverse tick
rounding, commission, partial fills, zero volume, expiry, cancellation,
rejection, duplicate IDs, lifecycle transitions, and end-to-end ledger cash,
position, and NAV.

This is a deterministic bar model, not a claim about queue priority, intrabar
sequencing, market impact beyond the configured formula, or achievable fills in
real markets. L2/L3, auctions, exchange matching rules, and live routing are out
of scope.
