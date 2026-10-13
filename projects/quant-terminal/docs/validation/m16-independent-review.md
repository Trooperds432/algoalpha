# M16 independent security review

Reviewer: `/root/m16_final_security_review`, an independent Codex review agent,
not a Paperclip employee run. Date: 2026-10-01. Production code and tests are
read-only to this reviewer; only this report is owned by the reviewer.

## Current verdict

**Pending final evidence.** The focused policy, protocol, worker, admission and
strategy-bridge checks pass. No critical or high containment defect was found in
the inspected production surface. Final backend revisions, their actual hostile
container run and the coordinator's integrated checks are still pending; this
report is not gate approval.

The coordinator subsequently redirected priority to owner-requested M17 work.
This review returns **Partial / pending**, releases its active agent slot, and
must be resumed against stable M16 code and fresh runtime/integrated evidence.

## Authoritative scope

The owner's brief, `docs/sources/owner-brief.txt:1414`, asks M16 to prepare the
architecture for safely running arbitrary Python; strategies should eventually
execute in isolated processes or containers. The owner exit gate requires that a
crashing or maliciously resource-intensive strategy cannot crash the terminal or
expose secrets. Public arbitrary-source rollout is a stronger, separate claim.

The present API/CLI always reject arbitrary source before queueing or staging.
The internal `IsolatedStrategy` bridge and operator-selected Podman backend are
the containment surface reviewed here. `JobManager` continues to execute only
trusted built-ins; it is not an adversarial sandbox.

## Review and direct focused evidence

Read the project instructions, roadmap, owner M16 brief, state, security contract,
16-case plan, all sandbox modules, public admission, trusted job admission,
strategy/engine metadata hooks and experiment replay code. Followed the
code-verification skill and its requirement/evidence matrix.

Command executed independently:

```text
.venv/Scripts/python.exe -m pytest tests/unit/test_sandbox_protocol.py tests/unit/test_sandbox_podman.py tests/unit/test_sandbox_worker.py tests/integration/test_m16_admission.py tests/integration/test_m16_strategy_bridge.py -q
```

Result: **50 passed**, 1 Starlette `httpx` deprecation warning, 1.94 seconds
reported by pytest. No hostile source executed on the host. These checks do not
prove actual OS containment. Final source hashes and integrated evidence will be
recorded after the backend writers stop.

A later independent command, `pytest tests/integration/test_m16_strategy_bridge.py
tests/integration/test_event_backtest.py tests/unit/test_sandbox_podman.py -q`,
passed **36 tests** in 2.04 seconds. It exercised the current trusted policy and
financial bridge; it was not a final integrated or containment gate.

The preserved initial actual-container result is
`m16-hostile-run-20261001A.json`: **28 passed, 2 failed, 0 skipped**. The failed
concurrency run exhausted configured subordinate-ID space and then the cleanup
health latch correctly blocked the redaction test. The runtime agent reports the
VM ID-space configuration was corrected, but no final rerun or independently
reviewed correction evidence has been received by this reviewer. That run is
**not** passing security-gate evidence. The final fixture now requires observed
strategy cancellation and parent-death cleanup within three seconds; these
strengthened fixtures also require a fresh run.

## Observations and rollout limitations

- Medium, high confidence, pending final revision: the initially reviewed
  `_mark_unhealthy` swallowed failure to create its durable marker, leaving only
  an in-memory latch. If an existing audit file is writable while its directory
  cannot create the marker, a new backend instance can lose the cleanup-failure
  state. The independent conmon limit still bounds strategy lifetime; nevertheless
  restart fail-closed policy requires durable health initialization or audit-backed
  recovery. This was reported to the coordinator before final checks.
- Medium, high confidence, pending final revision: `_stop_watchdog` accepted an
  already-exited watcher without checking its exit status. An unexpected watcher
  failure must make the job fail rather than returning a normal success solely
  because the strategy responded. This was reported to the coordinator; conmon
  remains the independent lifetime fallback.
- The bridge copies scalar allowlisted market fields through as-of feed access;
  it supplies neither feed/store/portfolio objects nor future observations. All
  returned proposals are revalidated, configured instrument metadata is resolved
  on the host, cross-instrument proposals reject, and ordinary engine risk and
  cash-reservation checks remain authoritative. Focused fixtures preserve the
  independently specified golden NAV and commission values.
- JSON is bounded, duplicate/unknown fields reject, source/data digests are
  checked, no executable deserialization is used, and malformed/nonfinite
  economic values reject. Nonempty third-party dependency declarations reject.
- Operator-selected images and seccomp profiles are trusted configuration. A
  digest provides immutability, not an independent guarantee that an arbitrary
  image is safe or free of embedded secrets. Public callers cannot choose them.
- Zero writable scratch is the supported backend policy; positive scratch
  budgets reject rather than claim an unenforced inode quota.
- Owner-label reconciliation is maintenance over all project-owned containers.
  It must run only when other operator jobs are quiescent; it is not a concurrent
  per-job recovery API.
- Public isolated scheduling, lease/restart status integration, queue admission,
  persisted isolated experiment replay and backend-incompatibility rejection are
  not implemented. Admission remains disabled. The strict 16-case plan must not
  be reported as wholly passed based on an operator backend suite.

## Required final evidence

Final runtime/control and hostile results, parent-death/cleanup evidence,
fresh full suite, unchanged financial fixtures, final source hashes and a
transparent per-case preparation-versus-rollout acceptance map are required
before an owner-scope verdict can be issued.
