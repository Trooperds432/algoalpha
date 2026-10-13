# M13 application, API and CLI implementation contract

## Objective and architecture assessment

Expose the verified M2-M12 research lifecycle headlessly. Existing engines and
immutable M8 storage are library APIs; real optimization/walk-forward application
adapters and a durable job boundary are missing. Add one application service used
by both FastAPI and the `quant` CLI. No financial formulas enter HTTP/CLI handlers.

## Requirements and proposed interfaces

`ResearchRuntime(root, project_root)` owns dataset/experiment stores, a fixed
trusted built-in strategy catalog, `prepare(payload)->ExperimentConfig`,
`run(config, run_id)->(ExperimentManifest, BacktestResult)`, and verified artifact
queries. Runtime state must be outside the project source tree. Strategy source,
project bundle and dependency lock are hashed before execution and checked after.
No uploaded executable Python is accepted in M13.

`ResearchCampaigns(runtime)` supplies `optimize(payload, job_id, record)`,
`walk_forward(payload, job_id, record)`, `monte_carlo(payload, job_id, record)` and
`robustness(payload, job_id, record)` returning canonical JSON-compatible reports.
`record` writes each trial/window/scenario to a durable job event journal.
Campaign requests include a nested `backtest` request, explicit dimensions,
objective/direction, constraints and budgets; no implicit best-strategy declaration.
Monte Carlo accepts verified source run IDs and derives its sample from artifacts,
never trusts client-supplied returns labeled as an existing run. Walk-forward
freezes training selections, applies the M10 OOS engine, and retains all run IDs.

M13 closes an adapter gap with the pinned `boundary_liquidation` boolean. The
named `scheduled_final_close_v1` convention liquidates at the final original
close with immediate publication strictly inside a single-instrument window;
it cancels pending orders, deducts ordinary final-bar fills from available
participation, and applies actual fees/slippage, notional/cash checks and ledger
fills. IS and OOS share the convention. Ordinary M5 behavior is unchanged when
false. This is an explicit scheduled auction model, not retrospective signal
execution; unequal opening/closing price and exhausted-capacity fixtures verify it.

`ResearchService.execute(kind, payload, job_id, record)` is the sole dispatch seam.
Data import accepts CSV text and explicit instrument/source/publication-delay
metadata. Queries return manifests, metrics, report tables/charts, verified
artifact bytes and multi-run comparisons preserving unavailable metrics.

HTTP resources: POST /backtests, /optimisations, /walk-forwards, /monte-carlo,
/robustness; GET /jobs and /jobs/{id}, POST /jobs/{id}/cancel; GET /runs and
/runs/{id}, /metrics, /trades, /orders, /fills, /equity, /artifacts; artifact
downloads; GET /strategies, /datasets; POST /datasets; POST /compare; GET /health.
Long operations return 202 with persistent job ID/status. CLI commands:
backtest, optimize, walk-forward, monte-carlo, robustness, data-import,
report, compare, runs, job, serve. JSON request files and exact Decimal strings
are the reproducible interchange format. CLI writes canonical JSON to stdout.

## Invariants and failure modes

Bounded local workers run separately from HTTP. Durable SQLite jobs preserve
queued/running/succeeded/failed/cancelled/interrupted outcomes and events. A
single scheduler lease per state directory prevents double dispatch. Shutdown,
worker crashes, restart and cancellation must leave explicit terminal states,
not successful partial artifacts. Completed M8 runs remain immutable even when a
parent campaign is interrupted. Initial jobs are trusted computation, not the M16
security sandbox. Worker environments do not inherit broker/model credentials.
Workers monitor scheduler process death (Windows process handle / POSIX parent
identity). Cleanup retains the lease until child termination is confirmed;
unexpected scheduler failure disables submission and reports unhealthy status.

Loopback-only serving, Host/Origin validation, no permissive CORS, body and queue
bounds, explicit structured errors and no arbitrary file paths from HTTP.
Artifact downloads use the M8 checksum/name checks. API cannot enable brokers.
IDs are server-generated; no pickle or execution of stored source. Unknown fields,
invalid budgets, nonfinite/nonnumeric finance values and unsupported strategy
choices reject. Monetary values remain decimal strings on every interface.

## Test plan

Real tiny-market backtests via CLI and API must produce matching result hashes.
Verify real optimization trials, IS/OOS source isolation and stitched equity,
Monte Carlo source sample binding, robustness reports and every required query.
Test invalid requests, tampered artifacts, failed/truncated jobs, cancellation,
restart recovery, queue capacity, unsafe origins/hosts/path names, exact decimal
serialization, and no broker side effects. Do not simulate financial adapters in
the end-to-end gate. Run the full existing golden suite and independent review.

## Files and sequence

1. application/runtime.py and focused real-run tests (one owner).
2. application/campaigns.py and campaign tests (one owner, fixed runtime interface).
3. application/service.py, jobs.py, worker.py, api.py, cli.py, __main__.py;
   API/CLI/job integration tests, packaging/dependency locks (coordinator only).
4. Synthetic request examples, usage documentation, independent review and gate.

Acceptance: all essential research operations runnable without GUI, shared service
parity, durable asynchronous job identity, verified result/artifact access, full
fresh checks and no unresolved high-severity finding.

References: FastAPI lifespan owns scheduler startup/shutdown
(https://fastapi.tiangolo.com/advanced/events/); heavy compute belongs outside
request background callbacks (https://fastapi.tiangolo.com/tutorial/background-tasks/).
Python spawn/process semantics inform Windows worker isolation
(https://docs.python.org/3/library/multiprocessing.html).
