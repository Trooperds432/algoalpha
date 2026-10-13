# Release candidate operations

The current candidate is **1.0.0rc1**, for local research and virtual PaperBroker.
Final v1.0 is gated by the recorded M16/M21 and role-interaction acceptance.

## Install a frozen candidate

Build the UI with `npm ci`, `npm run build`, and `npm test` from `apps/web`.
Build/verify a new local archive outside the source tree:

```powershell
uv run quant release-build --output C:/Users/Jonat/algoalpha/runtime/quant-terminal-1.0.0rc1.zip
uv run quant release-verify --archive C:/Users/Jonat/algoalpha/runtime/quant-terminal-1.0.0rc1.zip
```

Verify the recorded SHA-256 of the archive as well as the file manifest; checksums
detect corruption but do not authenticate an untrusted publisher. Extract the
verified archive into a new version-specific directory. From that directory run:

```powershell
uv sync --frozen
uv run quant --version
uv run quant portfolio-research --request examples/portfolio-risk-parity.json
uv run quant advanced-assets --request examples/asset-futures-settlement.json
uv run quant --state-dir C:/Users/Jonat/AppData/Local/AlgoAlpha/research serve
```

Use Python 3.12+ and the installed official `uv` tool. Dependency downloads require
access to the package registry; `--frozen` refuses a changed dependency lock.
The source archive contains the prebuilt UI and canonical engine source for
experiment provenance. It excludes runtime data, secret stores, employee roster,
node_modules, test tooling caches and the development environment. Do not run
from a directory inside runtime state or change the application tree during an
experiment. Sample inputs are synthetic and do not prove a strategy's profitability.

## Backup, restore and upgrade

Stop the terminal, isolated research scheduler and every CLI/paper writer first.
Offline backup explicitly checks the normal scheduler lease. The isolated scheduler
uses a separate `isolated-jobs/isolated-scheduler.lock`; stop it before copying state.
The backup command does not fence every possible writer or provide a live snapshot.
A leftover unlocked lockfile is harmless. Direct paper CLI writers have no shared
lease, so the operator must also stop those before copying state.

```powershell
uv run quant --state-dir C:/Users/Jonat/AppData/Local/AlgoAlpha/research backup --output C:/Users/Jonat/algoalpha/runtime/state-backup.zip
uv run quant restore --archive C:/Users/Jonat/algoalpha/runtime/state-backup.zip --destination C:/Users/Jonat/AppData/Local/AlgoAlpha/research-restored
uv run quant database-inspect --database C:/Users/Jonat/AppData/Local/AlgoAlpha/research-restored/jobs.sqlite --kind jobs
uv run quant database-upgrade --database C:/Users/Jonat/AppData/Local/AlgoAlpha/research-restored/jobs.sqlite --kind jobs
```

Restore requires an absent destination and performs integrity/hash/path/type checks
before making it visible. Keep backup outside source/state trees. Limits are 20,000
files, 128 MiB per file, 512 MiB total restored bytes, and 1 GiB archive; compression
ratio and malformed/link entries reject. This is local offline recovery, not encrypted
offsite backup or a live consistent snapshot service.

Schema kinds are `jobs`, `experiments`, `paper`, `risk`. Inspect each existing
SQLite database before an upgrade; apply explicit v0→v1 migration only to recognized
tables/columns. Future versions, corrupt SQLite or unknown schemas reject. Migration
preserves existing rows and is transactional. Retain the previous application folder
and original backup. Rollback restores that backup into another absent state directory
and starts the retained application against it; do not downgrade a newer live DB in place.

Include `isolated-jobs/jobs.sqlite` when inspecting and upgrading restored state,
using `--kind jobs`. Its succeeded-job records qualify isolated experiment reads;
preserving experiment files without their matching completion records is insufficient.

After installation/upgrade, check `/health`, `/ready`, one synthetic research request,
and a paper session snapshot. Scheduler restart marks claimed/running jobs interrupted,
preserves queued jobs and immutable registered experiments, and releases OS-owned
locks after process death. Paper sessions restart disconnected pending reconciliation.

## Observability and diagnostics

`/health` reports scheduler/log health; `/ready` additionally checks bounded SQLite
integrity. `/metrics` returns per-process status counts, a bounded rolling latency
sample and up to 32 server-error categories. `logs/runtime.jsonl` and one previous
rotation retain structured UTC events. Telemetry contains status/duration only,
never request bodies, headers, path/query strings or raw exception text. A log-write
failure latches unhealthy status until corrected and restarted. Logs are operational
diagnostics, not tamper-proof security audit; M16 audit is separate.

Use preserved validation reports for regression, vulnerability audit, performance,
load and security evidence. A zero advisory result describes the current registry
and scanned lock versions; it is not a guarantee against undiscovered vulnerabilities.

## Synthetic tutorial and unchanged strategies

`examples/seed_research.py --state-dir <outside-source-dir>` imports a labelled
synthetic dataset and writes backtest/optimisation/walk-forward request examples.
The trusted catalogue is `buy_and_hold`, `moving_average_cross`, `simple_momentum`;
their Python implementations are in `src/quant_terminal/backtesting/strategies.py`.
Follow `docs/headless-usage.md` and `docs/paper-trading-usage.md` for reports and
virtual sessions. The M21 lifecycle regression links the research stages and
unchanged strategy to PaperBroker; it does not forward-test through Trading 212.
