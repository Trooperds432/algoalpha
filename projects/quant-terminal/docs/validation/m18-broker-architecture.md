# M18 broker architecture acceptance

**Result: PASS for demo-only broker architecture.** This gate implements and
validates Trading 212 protocol mapping, reconciliation, and pre-trade risk controls.
It does not connect to Trading 212, submit broker orders, or forward-test a
strategy.

## Acceptance evidence

- The adapter builds typed request descriptions and strict response parsers against
  the checked-in Trading 212 v0 Beta OpenAPI bundle. The pinned snapshot is
  `docs/references/trading212-openapi-v0-20261002.json`, SHA-256
  `A272F70A713FA9F2F906E9B4BE12136C9D5D63D7F9F48562E939AA35C4FC041D`.
- Provider historical order/fill rows are aggregated only after checking provider
  order identity, local intent, supported side/status, unique fill IDs, and exact
  cumulative filled quantity. Hash-bound aggregates and durable local/provider
  bindings must agree with persisted fill IDs and quantities before reconciliation
  can release risk reservations.
- Pre-trade admission checks and durable duplicate/ambiguous-order protections
  retain exposure on unresolved or mismatched state. Risk release requires matching
  provider order state, broker account/position snapshot, local risk snapshot, and
  recorded provider fills. The durable kill switch is present; operator control is
  not exposed through the disabled API/UI.
- API/UI broker status is architecture-only Demo; Live is unavailable. Connection
  and order endpoints remain disabled before credential/state writes or transport.
  No real credentials were read or stored; no network transport, broker API call,
  order, or strategy forward test was used for this gate.
- The independent reviewer returned PASS for M18's demo-only architecture gate and
  verified the pinned OpenAPI hash and request/rate-limit mapping. Review covered
  source, fail-closed API/UI boundary, reconciliation evidence, and restart/mismatch
  tests. The review's focused adapter/risk/API-boundary suite passed 92 tests.

## Verification

After the final risk-state check and targeted provider-status regression, the
coordinator reran the combined adapter, risk, broker-boundary, and API/CLI tests:
**95 passed**, with one existing Starlette deprecation warning. The independent
reviewer separately ran **92 focused tests**.
The repository-wide `uv run python scripts/check.py` gate passed before that final
risk-state check: **444 passed, 32 skipped**, one Starlette deprecation warning;
Ruff, formatting, strict project typing, and dependency checks were clean. Thirty
skips are the opt-in hostile-container M16 security cases and two are Windows
symlink-creation limitations. Because the risk-state check landed after that full
run, the latest combined suite is the fresh verification for the final code; the
whole project gate should be rerun after subsequent source changes.

M16 remains in progress and unpassed. Arbitrary strategies remain outside the
broker workflow until that gate passes. Future runtime activation requires a
separately authorized scope, transport implementation, and renewed security and
reconciliation review.
