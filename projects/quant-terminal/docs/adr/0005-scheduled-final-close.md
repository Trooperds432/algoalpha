# ADR-005: Explicit walk-forward boundary liquidation model

Status: Accepted for M13. Date: 2026-09-28.

## Context

M10 requires each OOS window to begin with prior terminal NAV as cash. The M5 event
engine uses next-original-bar fills; it cannot fill an order after the final OOS
bar without leaving a position. Using the close as an ordinary market fill after
seeing that same close would introduce a retrospective signal.

## Decision

`boundary_liquidation=true` is pinned in every affected M8 config. A deterministic,
pre-scheduled `scheduled_final_close_v1` broker operation closes actual residual
inventory at the final eligible original bar close. It only accepts one instrument
with immediate publication strictly within the declared interval. It cancels
pending orders, deducts normal fills from the same bar participation capacity,
applies the configured fee/slippage/tick and cash/notional controls, posts the
actual priced fill through the ledger, and fails when capacity is insufficient.
It suppresses the final strategy callback; there is no signal-driven trade at a
previous price. Runs without this flag retain M5 execution semantics.

## Alternatives

Carrying inventory between windows would require M10 to define cross-window open
orders, market marks, margin and funding. Using the final closing price as an
ordinary order execution would violate the M4 next-bar rule. Refusing all
non-flat windows makes real selected buy-and-hold runs unusable.

## Consequences

The close is an explicitly simulated closing auction, not a claim that a real
broker could route that order at the same price. Immediate final-bar publication,
one-instrument scope and sufficient same-bar capacity are mandatory. Separate
fixtures use different open/close prices, slippage, commission and consumed
capacity. Future multi-asset or delayed-publication liquidation requires another
versioned model and validation.
