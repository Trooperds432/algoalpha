# M3 portfolio and accounting contract

## Scope and accounting policy

Implement a single-currency, event-driven cash and position ledger for cash
equities and ETFs. Ledger inputs are immutable execution fills; the ledger is the
only owner of cash, lots, realized P&L and marks. Orders, fees and execution
decisions remain outside M3. A fill is applied once by unique FillId; duplicate
fills reject without changing state. All values are Decimal parsed from decimal
strings, with 34-digit arithmetic precision and ROUND_HALF_EVEN. Fill prices and
quantities must already satisfy the instrument tick and quantity step; M3 rejects
bad fills rather than modifying execution facts.

Position accounting uses signed FIFO lots. A buy increases signed quantity and
reduces cash by quantity × price + fee. A sell decreases signed quantity and
increases cash by quantity × price - fee. Closing quantity realizes price P&L
against the oldest opposite-side lot. Entry and exit fees are allocated by
matched quantity; any finite-precision residual is assigned to the final
allocation so allocated fees reconcile exactly to charged fees. Unclosed entry
fees remain attached to open lots and reduce unrealized P&L. Fees on unfilled
orders do not exist at this layer.

Short positions are rejected unless the ledger is explicitly configured with
`allow_short=True` and a nonempty research-only financing/borrow assumption.
This documents an accounting scenario; M3 does not accrue borrow cost, financing,
margin or collateral. A long-only ledger cannot sell beyond its current quantity.
Negative cash is representable for accounting but must be constrained by later
risk and execution gates.

For one supported currency, position market value is signed quantity × mark,
NAV = cash + sum(signed market values), long exposure is the sum of positive
market values, short exposure is the absolute sum of negative market values,
gross exposure = long + short, net exposure = long - short. Leverage is gross
exposure / NAV when NAV > 0; otherwise it is null with an explicit reason.
Unrealized P&L includes open-lot fees. Realized plus unrealized P&L must equal
NAV less initial capital, within the explicitly documented Decimal arithmetic
policy. Marks default to the last fill price and change only through explicit
mark events. Stale-mark policy and corporate-action accounting are outside M3.

## Test design and examples

Hand-calculate and verify long entry/exit, short entry/cover with declared
assumption, partial close, multiple FIFO lots, reversal, fill fees, marking,
cash, signed position, exposures and NAV. Test duplicate IDs, currency mismatch,
unknown marks, invalid ticks/steps, unpriced/zero prices, insufficient long
quantity, unauthorized short, NAV at/below zero and exact fee residual handling.

Example: start with 10,000; buy 10 at 100 with fee 1. Cash=8,999, market
value=1,000, NAV=9,999, realized=0, unrealized=-1. Mark at 110: NAV=10,099
and unrealized=99. Sell 4 at 110 with fee 0.4: cash=9,438.6, remaining
quantity=6, market value=660, NAV=10,098.6, realized=39.2 and unrealized=59.4.
Thus realized + unrealized = NAV - initial cash = 98.6.

Reversal fixture: with shorting enabled by explicit research assumption, after
the same opening buy, sell 15 at 110 with fee 1.5. The first 10 close the long
and realize 98 after 1.0 exit fee and 1.0 allocated entry fee; the remaining
5 open a short at 110 with 0.5 entry fee. At mark 110, NAV=10,097.5,
realized=98, unrealized=-0.5, and total P&L=97.5.
