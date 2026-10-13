# M21 production release hardening

The owner requested M21 after M20 passed. Implement the owner release checklist:
security review, current dependency audit, explicit database upgrades, offline
backup/restore, crash recovery, bounded structured logs/metrics/error monitoring,
readiness, performance/load verification, install/upgrade procedure, documentation,
sample strategies/tutorial and the end-to-end research-to-paper acceptance.

Release Candidate 1 uses `1.0.0rc1`, freezes features after security/critical fixes
and regression evidence, and is a locally built source/UI artifact. Building is not
publication or deployment. Final `1.0.0` must remain withheld until all mandatory
security and acceptance evidence is complete. M16 and the all-role interaction
acceptance cannot be inferred from skips or the planned 370-role roster.

Supported release is local research and virtual PaperBroker. No external broker
connection, strategy forward test or order is authorized. Public arbitrary-source
admission remains disabled unless the full current-platform M16 gate independently
passes and its intended runtime rollout is separately implemented and reviewed.

Storage operations require every writer stopped. Backups hold the scheduler OS
lease but cannot fence direct paper CLI writers, so that precondition is explicit.
Snapshots include bounded SQLite backup copies, immutable artifacts and a versioned
checksum manifest. Restore validates path/type/count/size/hash/SQLite state before
promoting a staged tree into an absent destination. Existing data is never replaced.
Known database schemas upgrade v0→v1 transactionally; unsupported/future schemas
reject. Explicit migrations cannot execute caller-provided SQL.

Runtime telemetry uses bounded status/latency counters, recent server-error
categories and rotated JSONL operational logs. Request bodies, credentials,
headers, query strings, paths and raw exceptions do not enter telemetry. Failed
log writes latch unhealthy status. Metrics are per-process; sandbox required
security audit remains separate and fail-closed. Storage readiness probes are
read-only; request status metrics include denied/error outcomes.

Installation extracts a verified versioned source/UI candidate into a new immutable
application directory and uses `uv sync --frozen` for its portable lockfile. Runtime
state stays outside application source. Upgrades stop writers, take/verify backup,
install to a separate directory, inspect/upgrade supported DB schemas, run smoke
checks and retain the old binary/state backup for rollback. No global Python,
user credential store or automatic deployment changes.

Owner final acceptance is a single unchanged Python strategy through historical
dataset, execution/accounting backtest, report, parameter optimisation, walk-forward
OOS, Monte Carlo, robustness, comparison and HTML report, then the same strategy
business logic through virtual PaperBroker. Financial path differences must be
explained rather than silently altering economics. Actual run IDs and artifacts
are evidence; assignments and worker assertions are insufficient.

Gate requires fresh full regression, independent review, recorded performance/load
and installation/recovery evidence, all mandatory security acceptance and candid
remaining limitations. No release claim is permitted by merely changing versions.
