# M14 research terminal UI contract

## Objective and boundaries

Expose the M13 research application through a local React/TypeScript interface.
The browser is a client of the same loopback API used by the CLI. It does not
calculate returns, risk metrics, candidate rankings, or order fills. No brokerage
workflow exists in M14.

## Workspaces

- Dashboard: scheduler health, recent jobs and experiment access.
- Strategies and Data: trusted built-in strategy catalog, stored snapshots and
  explicit CSV import metadata.
- Backtests: exact-string configuration, job status, equity/performance charts,
  summary metrics and the report tables for Overview, Trades, Orders, Fills,
  Positions, Drawdowns, Monthly Returns, Execution and Logs.
- Optimisation: dimensions, objective, constraints, trial progress and results.
  Heatmaps and response surfaces are shown only when the recorded trial parameter
  structure supports them.
- Walk Forward: IS/OOS windows, frozen selections, per-window metrics and stitched
  OOS equity from returned campaign evidence.
- Monte Carlo: source-run selection, method controls, fan/percentile and terminal
  risk displays from recorded simulation results.
- Experiments: verified run list, reports and comparison, with warnings carried
  through and no automatic strategy promotion.

## Interface and invariants

The UI uses `/health`, `/strategies`, `/datasets`, `/runs`, `/jobs`, the M13
operation endpoints and `/compare` through same-origin requests. Long operations
display the returned job ID, poll status, expose failure/cancellation and retain
links to registered runs. Form financial values stay strings until submitted.
Charts are renderings of server values; chart axes and metric assumptions remain
visible. Missing or undefined metrics display as unavailable. The UI labels
synthetic datasets and does not imply research output is a strategy approval.

The production build is served under `/ui/` by the local FastAPI application.
The development server proxies API requests to the loopback backend. Neither
path admits remote origin requests or order submission. If frontend assets are
absent, `/ui/` reports an explicit unavailable response while API/CLI still work.

## Validation

Use type checking, frontend tests for response/interaction states, and a production
build. Exercise the built UI against a local synthetic M13 state in a browser:
load all workspaces, submit a backtest, inspect its charts/tables, run or inspect a
campaign, and compare two verified runs. Verify API/CLI regression suite and
same-origin behavior. Finish with an independent review and evidence report.
