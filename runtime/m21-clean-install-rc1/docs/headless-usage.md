# Headless research terminal

Run from the project folder in PowerShell. Runtime data lives outside this source
tree so changing a job record cannot change the source fingerprint. These examples
use generated synthetic prices, not broker data or a strategy forward test.

```powershell
$state = Join-Path $env:LOCALAPPDATA 'AlgoAlpha\research'
.\.venv\Scripts\python.exe examples/seed_research.py --state-dir $state
.\.venv\Scripts\python.exe -m quant_terminal --state-dir $state backtest --request "$state\example-requests\backtest.json"
.\.venv\Scripts\python.exe -m quant_terminal --state-dir $state optimize --request "$state\example-requests\optimize.json"
.\.venv\Scripts\python.exe -m quant_terminal --state-dir $state walk-forward --request "$state\example-requests\walk-forward.json"
```

Each command prints its durable job ID to stderr and the completed job/result JSON
to stdout. Nonzero exit status means failure or interruption. Read a job with
`python -m quant_terminal --state-dir $state job JOB_ID`; add `--cancel` to request
cancellation from another process. Runs already registered before cancellation
remain immutable. A completed run is not a promoted or approved strategy.

Start the loopback server with:

```powershell
.\.venv\Scripts\python.exe -m quant_terminal --state-dir $state serve --port 8765
```

Only one scheduler can own a state directory. While the server is running, submit
jobs through its API; stop it before using the standalone execution CLI against
the same directory. Read-only CLI queries can coexist. The server binds 127.0.0.1,
rejects foreign Hosts/Origins and is not an Internet deployment. JSON bodies are
bounded; floats and nonfinite financial values reject. Research worker processes
receive an allowlisted environment, not broker/model credentials. They remain
trusted built-in workers; this is not the M16 arbitrary-Python sandbox.

API POST resources accept the same request JSON: `/backtests`, `/optimisations`,
`/walk-forwards`, `/monte-carlo`, `/robustness`. Each returns HTTP202 with `id` and
`status`; poll `/jobs/{id}` and `/jobs/{id}/events?after=0&limit=100`. A malformed
research specification is retained as a failed job. Transport/type/size errors
reject directly. POST `/jobs/{id}/cancel` with application/json requests cancellation.
GET `/health` exposes scheduler health and disabled broker order capability.

Data: POST `/datasets` with exact `csv`, `source`, `publication_delay_seconds`, and
instrument metadata (instrument_id, symbol, currency, asset_class, calendar,
timezone, tick_size, quantity_step). CSV columns match M2; no filesystem path or
executable strategy upload is accepted. GET `/datasets` and `/strategies` provide
verified dataset and fixed built-in strategy catalogs. Supported strategy names:
buy_and_hold, moving_average_cross (fast_period/slow_period), simple_momentum
(lookback); all use instrument_id and exact-string quantity.

GET `/runs`, `/runs/{id}`, and `/runs/{id}/{section}` expose verified manifests,
metrics, trades, orders, fills, equity, positions, drawdowns, and report/artifact
records. `/runs/{id}/report.html` returns the complete M7 report with a restrictive
content policy. `/runs/{id}/artifacts/{name}` downloads checksum-verified bytes.
CLI `report RUN_ID --output PATH` writes HTML without overwriting an existing file.
CLI `compare RUN_A RUN_B` or POST `/compare` with `{"run_ids":["RUN_A","RUN_B"]}`
preserves unavailable metrics and warns about differing currencies, periods or
analytics conventions. No comparison declares a strategy best.

Monte Carlo request example, replacing RUN_ID with a verified completed run:

```json
{"source_run_ids":["RUN_ID"],"method":"iid_return_bootstrap",
 "sample_kind":"periodic_return","path_count":100,"path_length":40,
 "seed":17,"periods_per_year":252,"currency":"USD","initial_equity":"10000"}
```

Save it and run `monte-carlo --request PATH`. Samples are extracted from verified
report artifacts. The seed and resampling assumptions bind every output; no
client-supplied return array can masquerade as a source run. Moving-block and
closed-trade resampling retain the M11 sample/horizon restrictions.

Robustness request example (same-source baseline required):

```json
{"baseline_run_id":"RUN_ID","scenarios":[{"scenario_id":"double-fees",
 "kind":"commission","changes":{"execution_assumptions.commission.per_unit_rate":"0.2"},
 "rationale":"Adverse cost sensitivity"}]}
```

Walk-forward pins `boundary_liquidation=true` in both training and OOS runs.
`scheduled_final_close_v1` is an explicitly scheduled closing-auction simulation:
one instrument, final completed close strictly inside the window with immediate
publication, actual residual inventory, configured slippage/fees and remaining
final-bar participation. Pending orders are cancelled; insufficient capacity or
cash fails the window. Ordinary M5 market orders still use next-bar execution.
This convention differs from executing a newly observed signal at a past close.

Source/dependency changes invalidate replay of earlier pinned configurations.
Import/run a fresh control under the new source version before robustness tests.
Failed runs and jobs remain visible; uncertainty statistics do not establish
future alpha or permission to allocate capital.
