# M15 synthetic performance baseline (2026-09-30)

The M15 objective is to profile the correct event-driven implementation before changing it. This baseline exercises the existing CSV importer, checked snapshot query, event-driven backtest, and bounded optimiser. It makes no production-code or financial-semantic change, and does not connect to any broker.

## Reproduction

From the repository root on Windows:

```powershell
.\.venv\Scripts\python.exe benchmarks\m15_baseline.py --bars 390 --output benchmarks\baseline-390.json
.\.venv\Scripts\python.exe benchmarks\m15_baseline.py --bars 780 --output benchmarks\baseline.json
.\.venv\Scripts\python.exe benchmarks\m15_baseline.py --bars 1560 --output benchmarks\baseline-1560.json
# Additional process-memory run (preserves the original baseline JSON files):
.\.venv\Scripts\python.exe benchmarks\m15_baseline.py --bars 390 --output benchmarks\baseline-390-process-memory.json
.\.venv\Scripts\python.exe benchmarks\m15_baseline.py --bars 780 --output benchmarks\baseline-780-process-memory.json
.\.venv\Scripts\python.exe benchmarks\m15_baseline.py --bars 1560 --output benchmarks\baseline-1560-process-memory.json
```

The script creates and removes its own temporary dataset. It generates exact, flat-price one-minute bars on consecutive scheduled XNYS sessions from 2026-01-05. The fixture SHA-256 and resulting snapshot content hash are recorded with each result. A no-order, hold-cash strategy checks that every NAV stays exactly USD 100,000; the three optimisation trials each replay that backtest. Reported event throughput counts source bars divided by backtest wall time. Optimisation throughput is three trials divided by optimiser wall time.

The environment was Windows 11 build 26200, AMD64, Intel Family 6 Model 197, Python 3.13.15. Times use `perf_counter`; Python allocation peak uses `tracemalloc`. A second run of the harness (schema version 2) adds Windows `GetProcessMemoryInfo` working-set checkpoints and process-lifetime peak working set. It uses only the standard library and returns null process-memory fields on non-Windows hosts. The working set includes native allocations, loaded libraries, and runtime overhead. Its checkpoint is sampled after each measured operation, so it can miss a short-lived stage peak. The Windows peak field is cumulative from process start and cannot be attributed wholly to a given operation. Tracing remains active during timed operations, so these are comparative instrumented times rather than uninstrumented latency targets. Filesystem cache, CPU load, and thermal state were not fixed; repeat on target hardware before setting budgets.

| Bars | Import s | Query s | Backtest s | Bars/s | Backtest Python peak MiB | Optimiser s | Trials/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 390 | 0.399 | 0.208 | 0.326 | 1,198 | 1.02 | 0.943 | 3.18 |
| 780 | 0.512 | 0.343 | 0.563 | 1,387 | 1.93 | 3.119 | 0.96 |
| 1,560 | 0.902 | 0.663 | 1.557 | 1,002 | 3.87 | 10.459 | 0.29 |

The new process-memory run preserved the original results. Its working-set evidence, in MiB, is:

| Bars | Backtest end checkpoint | Optimiser end checkpoint | Process-lifetime peak after optimiser |
| ---: | ---: | ---: | ---: |
| 390 | 86.07 | 89.21 | 91.96 |
| 780 | 89.78 | 94.57 | 97.20 |
| 1,560 | 95.25 | 101.59 | 103.23 |

Raw process-memory evidence: [390 bars](../../benchmarks/baseline-390-process-memory.json), [780 bars](../../benchmarks/baseline-780-process-memory.json), [1,560 bars](../../benchmarks/baseline-1560-process-memory.json).

The 1,560-bar three-trial optimisation run took more than three times the 780-bar run, while the single backtest took about 2.8 times as long. This is an observed scaling concern, not a diagnosed cause. The benchmark's optimisation evaluator and recorder are deliberately in memory, so the numbers exclude M8 durable writes, M6 full analytics, report generation, API/worker overhead, and UI rendering. They cannot be presented as production campaign throughput. The flat, no-order strategy also excludes fill, inventory, and risk-path costs.

A separate 1,560-bar `cProfile` run identified cumulative costs across the whole harness: `BacktestEngine.run` 27.1 s, `DatasetStore.query_bars` 14.8 s, `DatasetStore.load_snapshot` 9.2 s, row-to-Bar conversion 7.2 s, and `PortfolioLedger.snapshot` 5.4 s. Querying re-resolves and validates the immutable snapshot; its cost is repeated for each backtest. This identifies the data query/validation path as the first optimisation candidate, but it is also a correctness boundary and must retain equivalent integrity checks. The `cProfile` times are heavily instrumented (37.3 s total) and must not be compared directly with the baseline wall times. The raw profile is [profile-1560.pstats](../../benchmarks/profile-1560.pstats); its JSON result is [profile-run-1560.json](../../benchmarks/profile-run-1560.json). To reproduce:

```powershell
.\.venv\Scripts\python.exe -m cProfile -o benchmarks\profile-1560.pstats benchmarks\m15_baseline.py --bars 1560 --output benchmarks\profile-run-1560.json
.\.venv\Scripts\python.exe -c "import pstats; pstats.Stats('benchmarks/profile-1560.pstats').strip_dirs().sort_stats('cumtime').print_stats(35)"
```

Before any optimisation, compare an uninstrumented replay and confirm the bottleneck with an order-producing strategy. Preserve the exact NAV/orders/fills of existing golden fixtures and a representative campaign before and after each change; no vectorised path may silently alter event ordering or point-in-time availability.

Evidence: [390 bars](../../benchmarks/baseline-390.json), [780 bars](../../benchmarks/baseline.json), [1,560 bars](../../benchmarks/baseline-1560.json), [harness](../../benchmarks/m15_baseline.py).
