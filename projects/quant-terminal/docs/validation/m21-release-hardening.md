# M21 release hardening evidence

Status: engineering deliverables implemented and local **1.0.0rc1** candidate verified. M21 stays in progress; final 1.0 remains withheld.

| Requirement | Fresh evidence and result |
| --- | --- |
| Security review | Independent path/archive review findings fixed; security report retains blocked M16 hostile-runtime preflight. Not a security gate pass. |
| Dependency audits | `m21-python-audit.json`: 20 production Python dependencies, no known advisories. `m21-node-audit.json`: zero known advisories after Vitest 4.1.11 patch for GHSA-82fw-gwwq-j7x9. |
| Database upgrade | Explicit known-schema v0 to v1 transactional CLI migration; future/unknown schemas reject; restored queued jobs and actual installed-state upgrade checked. |
| Backup/restore | Offline bounded SQLite snapshots and checksum manifests; staged absent-destination restore; no overwrites. Tests cover traversal, aliases, links, corruption, count/size and active scheduler. Actual installed state: 12 files restored, registered runs unchanged. |
| Crash recovery | Queued jobs preserved, claimed jobs interrupted, OS-owned scheduler leases reacquired; existing paper restart requires reconciliation. |
| Observability | Per-process bounded metrics/error categories, rotated structured logs without request secrets; log failure latches unhealthy; readonly bounded database readiness. |
| Performance/load | `m21-performance.json`: 390 synthetic bars, backtest 0.236s / about 1655 bars/s, optimisation 0.683s. Scope is single-process benchmark, not exchange capacity. 100 concurrent ASGI requests across four workers passed within 30s; no production/network-capacity claim. |
| Installation/upgrade | Corrected candidate clean-installed with `uv sync --frozen` in a separate directory; version, two research CLI examples, seeded backtest, HTTP health/readiness/metrics/UI assets, offline backup/restore and schema upgrade pass. Commands/results in `m21-install-smoke.json`. |
| Docs/tutorial/sample strategies | `release-operations.md` covers versioned install, offline backup, migration, rollback, telemetry and synthetic tutorial; trusted sample strategies retained. |
| Unchanged strategy lifecycle | `m21-lifecycle-evidence.md` and actual manifests/hashes in `m21-lifecycle-artifacts.json`. Historical research through reports/OOS/MC/robustness/comparison and same strategy through virtual PaperBroker. |
| Full regression | `checks-20261002T211741956812Z.json`: 642 passed, 32 skipped; lint, formatting, types and dependency consistency pass. 30 hostile-container opt-in skips and 2 unavailable Windows symlink cases explicitly retained. Frontend 12 tests pass; production build passes. |
| Independent review / final release | `m21-independent-review.md`: storage PASS after fixes; final 1.0 NO-GO. M16 and 370-role participation/interaction remain mandatory unresolved acceptance. |

Corrected frozen archive: `C:/Users/Jonat/algoalpha/runtime/quant-terminal-1.0.0rc1-m21-fixed.zip`, SHA-256 `43d937aa5ade9a5fce72934f22fe1de91f73be51114cfb5a331f9455f2fd4d06`, 119 payload files plus manifest. Manifest explicitly sets final_release=false. Checksum verification detects corruption, not publisher authenticity. Clean install: `C:/Users/Jonat/algoalpha/runtime/m21-clean-install-rc1-fixed`.

The first candidate omitted `requirements-dev.lock`, needed for experiment provenance; clean-install backtest failed. Original archive, failed job and state remain preserved and referenced in installation evidence. Corrected archive includes the portable lock; an assertion prevents recurrence, full checks reran and clean-install backtest succeeded. No golden economic outputs changed.

Remaining final-release work: recover the existing Windows Podman remote connection, independently rerun real M16 hostile lifecycle/cleanup/audit acceptance and intended runtime rollout; record actual role-specific Paperclip runs/artifacts and terminal interactions for all 370 roles. Ordinary test skips, planned assignments and collaborator summaries cannot close these gates. Public arbitrary Python remains disabled. Vite warns about the Plotly chunk size, and Starlette emits a test-client deprecation warning; these are retained, not hidden. Browser visual QA and hosted CI/deployment were not performed or claimed.
