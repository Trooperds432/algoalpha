# Testing and quantitative validation

Write failing tests before/alongside each implemented contract. M0 is documentation
and scaffolding: no financial production code or numeric correctness claim yet.
M1 establishes pytest, Hypothesis, ruff, mypy and automatic CI. Passing a tool invocation
is not passing its intended behaviour. Retain commands, exit statuses and outputs.

## Golden Quant suite
Use 20–50 tiny synthetic markets with manually predetermined orders, fills, fees,
cash, signed lots, realised/unrealised P&L and NAV. Initial catalogue:
1 long round trip; 2 short cover; 3 partial exit; 4 multiple entry fills; 5 reversal;
6 proportional fees; 7 order minimum across partial fills; 8 market gap; 9 buy limit;
10 sell limit; 11 stop gap; 12 deferred stop-limit; 13 shared volume; 14 cancellation;
15 expiry; 16 rejected buying power; 17 split wealth conservation; 18 dividend
receivable/pay-date; 19 missing bar; 20 stale mark; 21 complete loss; 22 negative NAV;
23 constant returns/undefined Sharpe; 24 unrecovered drawdown; 25 duplicate fill;
26 no future access; 27 symbol reuse; 28 DST/holiday; 29 OOS exclusion; 30 seeded bootstrap;
31 partial fill then residual risk rejection/reservation release; 32 adverse buy/sell
tick boundaries; 33 economic-time versus observed-time callback causality.

First independent arithmetic fixture: initial cash 1000, buy 4 at 100 with fee 1:
cash=599, holdings=4; mark=105 gives NAV=1019, unrealised gross P&L=20. Sell 2 at 110
with fee 1: cash=818, holdings=2, remaining entry fee allocation=0.5; mark=108 gives
NAV=1034. Gross realised=20, gross unrealised=16, total fees=2, net wealth change=34.
Closed quantity net P&L=20-0.5-1=18.5. The remaining entry fee belongs to open lots.
This fixture deliberately distinguishes cash fees, lot allocation and marked wealth.

Split fixture: cash=100, quantity=10, mark=50, NAV=600; 2-for-1 split results quantity=20,
mark=25, NAV=600. Independent dividend fixture starts cash=100, quantity=10, mark=50;
mark falls from50 to49 at ex-date,
receivable=10; NAV remains600 before other market moves; pay-date transfers10 to cash.
Drawdown fixture NAV=[100,120,90,108,120]: peak drawdown=25%, trough at third observation,
recovery at fifth; session-close returns=[0.2,-0.25,0.2,1/9].

Golden changes require explicit rationale and approval; never regenerate expected
economics directly from the implementation being tested. Risk, accounting and validation
owners independently sign fixtures. Unsupported scenarios are planned, not fake passes.

## Test layers
Unit: formulas, states, timestamp comparisons and failure cases. Property: conservation,
no oversell in cash policy, total fills <= order quantity, deterministic ordering and
nonnegative cash where mandated. Integration: real storage/ledger/callback chain, failed
artifact writes and replay. Differential: match simple BuyAndHold/MA rules against
LEAN or Backtrader with identical timing, fees, calendar and actions; explain every
discrepancy, do not blindly accept reference outputs. End-to-end: owner's final research
lifecycle plus unchanged strategy paper parity after M17. Performance after correctness.

Tolerance policy: exact Decimal cash/lot identities with rounding deltas separately
recorded; float analytics defaults atol=1e-12, rtol=1e-10 on ordinary unit fixtures,
metric-specific documented tolerance for ill-conditioned data. Economic artifacts
reproduce exactly where Decimal, toleranced where numerical; IDs/time excluded.

## Gate evidence
Each requirement links to contract, fixture/test, result and reviewer. Report confirmed,
partial, unverified or failed. Global done criteria include lint/type/unit/integration/
property/security/docs plus relevant performance and independent financial checks.
No unexplained skips. M0 records code checks as not applicable, not passed.
All 370 later interaction tests require actual actor/job/audit evidence; roster or
task creation alone cannot meet that requirement.
