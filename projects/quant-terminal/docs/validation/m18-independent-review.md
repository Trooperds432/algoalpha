# M18 independent review

**Recommendation: PASS for the demo-only broker-architecture gate.** Review was
performed independently after typed provider order/fill aggregation and durable
local-to-provider bindings were integrated.

The reviewer confirmed the earlier caller-supplied-fill gap is closed. Parsed
provider history rows must have unique fill IDs and exact aggregate quantity and
must match the provider order and local intent. Hash-bound aggregates are checked
against durable local/provider bindings and persisted fill IDs/quantities before
reconciliation releases a reservation. Partial/full fill restart and mismatch
regressions are covered. Risk admission retains unresolved exposure and the durable
kill switch is checked atomically.

The reviewer independently checked the pinned Trading 212 OpenAPI v0 Beta snapshot
and computed SHA-256
`A272F70A713FA9F2F906E9B4BE12136C9D5D63D7F9F48562E939AA35C4FC041D`, matching
`docs/m18-broker-contract.md`. The documented `/api/v0/equity/positions` route and
rate budget match the adapter and its schema fixture test.

Static review found no HTTP/socket/URL-opener/SDK use or caller path to the adapter.
Disabled API connection/order routes fail closed before creating state, credentials
are synthetic-only and redacted, and the UI distinguishes architecture-only Demo
from unavailable Live with both disabled. The reviewer ran focused adapter/risk/API
boundary tests (92 passed); the coordinator's latest combined test run after the
final risk-state check passed 94 tests.

Scope is architecture only: no network transport, scheduler, broker operation, or
strategy forward test. M16 remains unpassed and arbitrary strategies remain outside
the broker workflow.
