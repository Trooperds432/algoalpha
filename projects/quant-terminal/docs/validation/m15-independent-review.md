# M15 independent event-loop review (2026-09-30)

**Verdict: PASS for financial-semantic preservation of the current snapshot optimization.** The M15 milestone's broader performance/scalability work is still in progress. This review is read-only apart from this report; it used local synthetic data and made no broker connection or order submission.

## Reviewed change and invariant

`src/quant_terminal/backtesting/engine.py` now takes one portfolio snapshot after all fills and any scheduled boundary liquidation in an observed-time batch. It calls `ledger.mark` only for bars whose instrument has a nonzero position in that snapshot and takes a final snapshot only if a mark occurred. When no mark occurs, it reuses the pre-mark snapshot. A mark does not alter position quantities or cash; `PortfolioLedger.mark` validates the price, updates the position mark, and rolls back on failed snapshot construction. Thus the mark predicate remains stable over the batch, while every fill and boundary liquidation is reflected before the NAV point. Bar ordering, broker processing, cash reservations, benchmark observations, strategy invocation, and order admission are untouched by this change.

The no-mark branch is safe because its snapshot was taken after the batch's ordinary fills and boundary liquidation. The marked branch still records a final snapshot after all marks, so multi-instrument NAV reflects every mark. Existing `ledger.mark` calls still enforce tick-size and positive-price validation, including failure rollback.

## Fresh checks

From the repository root with `.venv` Python 3.13.15 on Windows:

| Check | Result | Material coverage |
| --- | --- | --- |
| `python -m pytest -q tests/integration/test_event_backtest.py tests/integration/test_application_runtime.py tests/unit/test_accounting_ledger.py` | 38 passed | Hand-calculated NAV, cash, fills, mark, multi-asset equal-time batch, reservations, risk rejection, and final-boundary liquidation/capacity. |
| `python -m pytest -q tests/integration/test_application_campaigns.py tests/integration/test_experiments.py` | 21 passed, 1 Windows symlink skip | Campaign replay, experiment retention and report-level economic behavior. |
| `python benchmarks/m15_baseline.py --bars 1560 --output %TEMP%/m15-independent-1560.json` | Completed; 1,560 bars, 1,561 exact USD 100,000 NAV points, 3 completed optimisation trials | Same fixture CSV SHA-256 `5ebb4960d6e538b73cb458d359355bf73000a31fa81dc08fcb9447a3ee1891a1` and snapshot hash `e75c0814fa79964b9465760641f55c06c91113f3977b5aaa2db73b5e2821a832` as recorded baseline and after artifacts. |

The committed `benchmarks/baseline-1560.json` records 1.557 s backtest and 10.459 s optimisation; `benchmarks/after-1560.json` records 1.038 s and 9.109 s respectively, with the same fixture and Python-traced peak allocation near 4.06 MB for backtest and 4.60 MB for optimisation. Those two recorded runs suggest a 33% backtest-time reduction and 13% optimisation-time reduction. My independent rerun measured 2.089 s backtest, 9.487 s optimisation, and 4.063 MB/4.595 MB Python-traced peaks. Since this single fresh backtest was slower than both recorded runs and CPU/cache conditions were uncontrolled, the size and repeatability of the speedup are **not established**. This does not affect the economic-preservation verdict; the flat no-order benchmark cannot establish fill-path performance.

## Scope and residual limits

The repository is currently untracked in Git, so there is no authoritative code diff or pre-change executable revision to differential-test against. The review compares the current control flow against ledger invariants and the existing hand-calculated golden tests, rather than asserting byte-for-byte equality with a preserved pre-change run. The benchmark checks exact hold-cash NAV but does not save full result fingerprints. A pinned before/after order-producing result and repeated controlled performance measurements would strengthen any claim of measured speed improvement. The Windows symlink skip is unrelated to this event-loop change.
