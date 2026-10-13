# M13 completion report

Status: **PASS**, 2026-09-28.

The local application service now exposes the verified research engines through a
loopback FastAPI API and the `quant` CLI. A durable SQLite job journal records
queued, running, terminal and interrupted states; bounded workers execute trusted
built-in strategies separately from HTTP requests. Backtesting, optimisation,
walk-forward, Monte Carlo and robustness share the same runtime and immutable
experiment store. Exact decimal values use canonical JSON strings. The synthetic
example workflow creates a labeled 43-session dataset and request files.

The M13 contract is [m13-api-cli-contract.md](../m13-api-cli-contract.md). The
application/job decisions are recorded in [ADR 0004](../adr/0004-local-application-jobs.md)
and [ADR 0005](../adr/0005-scheduled-final-close.md). The new scheduled final-close
option is explicit and leaves ordinary M5 backtest behavior unchanged when disabled.

Fresh full gate: [checks-20260928T064809897115Z.json](checks-20260928T064809897115Z.json).
Ruff lint/format passed; mypy passed for 67 source files; **253 tests passed**,
two Windows symlink capability tests skipped; dependency consistency passed.
The only warning was Starlette's `TestClient` / httpx deprecation notice.
Focused real runtime/campaign tests also passed (15 tests). Independent reviews
identified boundary and application adapter defects during implementation, which
were corrected before this full gate. No separate Paperclip employee review is
claimed from these Codex collaborator checks.

The API is local research infrastructure. It accepts no uploaded executable
strategy source, has no broker order route, and cannot submit Trading 212 demo or
live orders. M16 remains necessary before untrusted strategy execution.

Acceptance: **PASS** headless research operations; **PASS** shared CLI/API service;
**PASS** persistent job identity and verified result access; **PASS** full local
checks. **READY FOR M14**.
