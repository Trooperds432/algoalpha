# M1 foundation status

Implemented core immutable identifiers/events, UTC monotonic clock and snapshot replay,
exact Decimal serialization, validated configuration, structured redacted logging,
isolated Python environment, pinned development packages, package installation and CI commands.

Contracts: core modules are stdlib-only. No accounting/execution/backtest logic yet.
Run .venv/Scripts/python.exe scripts/check.py on Windows. scripts/watch_ci.py watches
source/test/config changes and automatically runs that same pipeline locally; --once
checks current state and exits. GitHub Actions configuration exists but has not been
published or run remotely. Local checks do not imply hosted CI success.

Current tests: 28 unit/property/integration cases passed before independent re-review.
Read dated checks JSON for current exact source hashes, commands and exit results.
Property tests cover monotonic replay and exact Decimal round trips. Integration
persists/reloads causal events and checks structured audit output.

Limitations: field-based log redaction cannot identify secrets embedded in arbitrary
string values. Never place secrets in free-text fields. This foundation is not a
strategy sandbox, financial engine or trading adapter.

The first independent QA review (ALG-374) found three blockers. Clock restore now
normalises malformed, non-string and naive snapshots to DomainError; its new test
failed before the fix and all four cases pass afterward. The domain contract now
assigns observed_at to generic events and available_at to source market bars,
with a JSON schema assertion. A real local watcher run detected an import-order
failure immediately after a test edit and then passed automatically after correction.
See dated checks-20260925T145246762097Z.json (failed) and
checks-20260925T145256268946Z.json (passed, 28 tests). This is local automatic
CI evidence; the GitHub workflow has not run remotely.

Gate PASSED after independent QA re-review ALG-375 on 2026-09-25. The reviewer
confirmed all three findings closed and ran a fresh five-stage check with 28 passing
tests; see paperclip-qa-m1-rereview.md and checks-20260925T145416980724Z.json.
Hosted GitHub CI has not run. The passed gate relies on documented local automatic
watcher evidence under the roadmap's local-automation allowance.
Both delegated implementation workers hit Codex account usage limits;
the coordinator implemented the foundation locally. They are not credited with work.
