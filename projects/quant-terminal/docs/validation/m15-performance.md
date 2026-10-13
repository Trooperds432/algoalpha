# M15 performance validation

Status: **PASS** (2026-09-30), with no repeatable speedup or production capacity claim.

The deterministic synthetic fixture, baseline harness and initial measurements are documented in [m15-baseline.md](m15-baseline.md). Function-level profiling of the existing 1,560-bar no-order run found repeated checked dataset query/validation and repeated `PortfolioLedger.snapshot()` work. The separate read-only profile review in [m15-profile-review.md](m15-profile-review.md) confirmed the data-query overhead on the 43-bar synthetic state.

The first targeted change reuses the prior portfolio cash while no ledger fill has occurred, takes one view after each fill batch, and reuses that view when no position was marked. It still rebuilds the view after marks. The event order, fills, Decimal accounting, and snapshot-integrity/DuckDB data boundary were not changed. No vectorised strategy or parallel execution path was added.

The same instrumented 390/780/1,560-bar harness measured the following. Values are single-run wall times under `tracemalloc`, so they are comparative observations and subject to machine/cache noise, not guaranteed throughput.

| Bars | Backtest before s | Backtest after s | 3-trial campaign before s | Campaign after s |
| ---: | ---: | ---: | ---: | ---: |
| 390 | 0.326 | 0.250 | 0.943 | 0.792 |
| 780 | 0.563 | 0.567 | 3.119 | 2.228 |
| 1,560 | 1.557 | 1.038 | 10.459 | 9.109 |

Before/after fixture SHA-256 and snapshot content hashes match at each size. The benchmark asserts exact USD 100,000 NAV at every event and all three campaign trials; it does not exercise orders, fills, durable journals or API/UI overhead. The exact financial integration/ledger tests passed (27 tests), and the full gate passed with Ruff lint/format, mypy on 67 source files, 253 tests passed, two Windows symlink capability skips and dependency consistency. Fresh evidence: `checks-20260930T195748152616Z.json`. Existing economic fixtures cover order and risk paths; the independent review must assess the reuse logic and those tests before the gate closes.

The benchmark now also records Windows process working-set checkpoints and process-lifetime peak memory for all three fixture sizes; those values and sampling limits are in [m15-baseline.md](m15-baseline.md). The original baseline files remain preserved. The independent economic review in [m15-independent-review.md](m15-independent-review.md) passed the snapshot reuse semantics and ran 59 affected/campaign tests (one Windows symlink skip). Its separate 1,560-bar timing was slower than either recorded run, so a repeatable speedup is **not** established. M15 accepts the safe reduction in redundant snapshot work, the profiling/baseline infrastructure and exact-result tests, rather than a throughput SLA.

The measured checked-query cost remains a future candidate. A persistent unchecked cache would weaken mutation detection; M15 does not add one merely to improve the benchmark. The data and campaign throughput samples do not justify a production capacity claim.
