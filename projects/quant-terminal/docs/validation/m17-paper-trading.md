# M17 paper trading gate

Status: **PASSED**, 2026-10-01, for the owner local paper architecture and
restart/accounting exit gate. The owner explicitly requested M17 ahead of unfinished
M16; this record does not approve M16 or public arbitrary-source execution.

Requirements: `docs/sources/owner-brief.txt:1444`, specified in
`docs/m17-paper-contract.md`. Usage: `docs/paper-trading-usage.md`.

## Implementation and acceptance

| Owner requirement | Implemented seam | Fresh evidence |
|---|---|---|
| Realtime DataFeed and live clock | `paper/feed.py`, `paper/clock.py`; UTC monotonic time, bounded visible history | 17 feed/clock tests |
| PaperBroker, unchanged strategy interface | Existing OHLC execution, immutable `StrategyContext`, common history protocol | Golden economics and historical regression suite |
| Persistent order state and restart | Atomic SQLite journal, exact ledger replay, projection/head validation; no strategy callback replay | Pending/partial order restart, actual CLI subprocess, API restart |
| Disconnect/reconnect | Halt fills/proposals; explicit acknowledgement of matching broker/cursor snapshot | Owner disconnect/reconnect cases |
| Position reconciliation | Compare cash, positions, orders and fills; mismatch does not overwrite accounting | Economic fixture and mismatching snapshot case |
| Stale/future/missing/out-of-order data | Validate observations/cursor before any execution or strategy callback | Each owner fault case, future and chronological cases |
| Duplicate events/fills | Exact duplicate has no economic effect; changed duplicate durably halts | Duplicate fill, concurrent same/different identity, repeat-fault cases |
| Crash/write failure/corruption | Commit economics atomically; read transaction snapshot, hash-chain and committed head | Injected SQLite abort/retry, state/head/tail corruption, restart |

Independent partial-fill fixture: initial USD10,000, buy10, volume8 at50%
participation produces quantities4/4/2 at101/102/103, commission0.10/unit with
minimum1 per order. Hand-calculated cash9595/9187/8981 and NAV9999/10003/10011
match exactly before and after restart; reconciliation error remains zero.

API/CLI use the same `PaperService`. The UI includes a Paper Trading workspace
for configuration, status, explicit reconciliation and disconnect. No sessions,
broker connections or strategy forward tests were launched outside disposable
synthetic test fixtures. Public custom source admission remains disabled.

## Fresh verification

- Coordinator affected tests: **42 passed**; independent affected retest:
  **42 passed** in2.07s, no M17 skips.
- `scripts/check.py`: `checks-20261001T173906465423Z.json`; all five commands exit0:
  Ruff lint/format, strict mypy, pytest and dependency checks. **352 passed,
  32 skipped**. Thirty skips are separately opt-in M16 hostile cases and two
  require Windows symlink privileges; none establish M16 containment or skip M17.
- `npm run build`: exit0, Vite20.52s. `npm test`: **12 passed**, exit0.
  Existing lazy Plotly chunk-size warning remains; existing Starlette/httpx
  deprecation warning remains. No dependencies changed.
- Independent review: `m17-independent-review.md`, **PASS**; checked source hashes
  against the final full-check artifact and found no unresolved material issue.

Preserved full-check failure: `checks-20261001T173635530102Z.json`; tests352pass
but Ruff's diff renderer panicked while reporting the unformatted M16 hostile
fixture. Formatting that file without semantic changes corrected the failure;
the subsequent complete run above passed. Independent economic review defects
and their regressions are retained in the reviewer report.

Live browser visual inspection could not run: CUA reported no available browsers
or apps. Compilation and API/CLI behavior are verified; visual browser interaction
is unverified and is not counted as gate evidence. No test server remains running.

## Actual agent participation

`/root/m17_feed`: Data Engineer role; implemented and tested clock/feed.
`/root/m17_validation`: independent Validation/Accounting role; wrote22 fault and
economic tests, exercised the terminal interfaces, reviewed hash-bound evidence.
`/root`: implementation/integration; transactional engine, service, API/CLI/UI,
failure corrections, full checks and gate recording. These are actual collaboration
tasks and artifacts, not claims of Paperclip employee runs or all370 interactions.

## Boundaries

One instrument/currency, trusted built-ins, long-only and bounded10,000-command
sessions. Exact simulated accounting, not live price/exchange fidelity. Market
data is pushed locally; no vendor subscription or background heartbeat. Staleness
blocks event admission. Database hashes are corruption checks, not authentication
against a database-writing attacker. Broker adapters remain M18.
