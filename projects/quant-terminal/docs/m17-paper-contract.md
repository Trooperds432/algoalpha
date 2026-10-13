# M17 paper trading contract

The owner explicitly requested M17 implementation on 2026-10-01 while M16 final
verification remained pending. This changes implementation priority; it does not
declare M16 passed. Public custom Python remains disabled.

Implement a local, bar-driven realtime paper session with the existing
`Strategy.on_bar(StrategyContext, Bar)` interface. A source adapter pushes completed
bars, with stable event IDs and a contiguous session-wide sequence. There is no
vendor connection, broker credential, automatic session start or strategy forward
test in this build. Tests use synthetic observations and virtual funds only.

Paper execution reuses the M4 OHLC simulator: orders emitted after a completed bar
can fill only on a subsequent eligible bar, with explicit commission, slippage and
volume participation. Initial release supports one configured equity/ETF, one
currency, long-only inventory, and the three trusted built-in strategies. Unsupported
strategies and modes reject before creating a session.

SQLite commands, their simulated execution projection and integrity hashes commit
atomically. Replay reconstructs orders, fills, cash, FIFO positions, marks, source
cursor and connectivity without invoking strategy callbacks. A crash before commit
leaves the prior state; a retry after commit is idempotent. Restart requires explicit
reconciliation before accepting new observations. Exact same duplicate events/fills
have no economic effect; changed duplicates, missing sequences, old updates,
future/stale observations and mismatching reconciliation block processing.

Reconciliation compares virtual broker cash, positions, order states and fill IDs
with the reconstructed ledger; mismatch never overwrites balances. Disconnect and
faults retain pending orders and halt fills/proposals. Reconnect acknowledges the
last durable cursor and matching virtual broker state; missing observations must be
backfilled in sequence. Old/stale backfill cannot execute; an operator must create a
new explicitly reconciled session rather than fabricate historical fills.

Gate evidence must cover all seven owner fault cases, exact accounting against a
hand-calculated fixture, restart with pending/partially filled orders, concurrent
writers, interrupted transactions, corrupt persisted state, API/CLI parity, fresh
full checks and independent review. State replay is deliberately bounded to 10,000
commands per session; no high-frequency throughput or external live feed claim.
