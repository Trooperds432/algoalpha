# M20 independent review

**Recommendation: PASS.** The reviewer examined asset-specific money, futures,
options and request modules, API/CLI/UI boundaries, requirements and fixtures.
It found one material boundary gap: terminal option payoff rejected spot zero.
The options agent fixed payoff to permit finite nonnegative terminal spot and
added long/short call/put zero-underlying fixtures; interior option pricing and
margin keep positive spot constraints.

Reviewed conventions include native-currency FIFO and fees, event-time external
flow translation, FX quote direction/revaluation, daily futures variation cash
without full-notional debit, supplied collateral/margin-call diagnostics, additive
non-tradable continuous rolls, supplied borrow availability/simple fees/financing,
European Black-Scholes/Greeks and uncovered single-leg equity margin. Unsupported
exercise/margin/roll modes reject. UI example requests are exercised against both
API and CLI; legacy instrument construction rejects FX/futures/options through
equity assumptions. Broker order functionality remains disabled.

The reviewer subsequently approved native account marks at zero (valuation only,
trade prices remain positive), checking the new total-loss bankruptcy fixture.
Final fresh combined M20 suite: **67 passed**, one upstream Starlette test-client
deprecation warning. Ruff and strict module typing passed during review.
The root records final full-suite and frontend evidence separately. Fresh browser
inventory exposed no enabled browser, so visual UI verification is unverified.

Actual collaborators were the root coordinator, the assigned futures agent,
options agent and independent review agent. No Paperclip employee run IDs were
produced or all-370 interaction votes claimed.
