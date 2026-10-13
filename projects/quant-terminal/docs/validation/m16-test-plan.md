# M16 security and strategy-isolation acceptance plan

These tests run with disposable state and synthetic market data only. No broker adapter, external service, or actual credential is contacted. A generated canary secret and canary host file stand in for protected data; their exact values must not appear in test output. Exercise the public API/CLI path as well as the backend entry point so admission cannot be bypassed. Keep the existing built-in strategy golden suite as a regression gate.

## Contract and admission

1. Submit the three trusted built-in strategies and verify economic outputs, manifest hashes, and normal job cancellation are unchanged.
2. On a host without a passing isolation capability probe, submit an `isolated_python` strategy. Assert rejection before execution, with an explicit unsupported-control reason and no run artifact, process or staging file. Confirm the API/UI do not advertise arbitrary Python as available.
3. Reject mode confusion: an arbitrary source path, source bytes, unregistered identifier, modified dependency hash, unsupported package/native extension and strategy package beyond size budget cannot enter `trusted_builtin` or bypass admission through CLI/API. Unknown fields and traversal/absolute paths reject.
4. Run the capability probe after a deliberate configuration failure (disable a mandatory control). Assert fail-closed status, no fallback to a weaker backend, and a structured audit reason.

## Confidentiality and authority attacks

5. A strategy tries `os.environ`, `/proc` or platform equivalents, command-line inspection, standard streams, inherited handles, and common credential-store locations. It must not obtain the canary or any host secret. Inspect both returned output and all audit/job logs for absence of the canary.
6. A strategy reads or writes the project tree, runtime `jobs.sqlite`, dataset store, another run's artifacts, user profile and canary host file using direct, `..`, absolute, symlink/junction/reparse-point and alternate path forms. All accesses fail; no protected content or bytes change. Permitted data is read-only and limited to the declared snapshot/window; future bars remain unavailable.
7. A strategy attempts DNS, TCP/UDP IPv4 and IPv6, loopback API access, localhost broker/demo endpoints and host IPC. All attempts fail without packets/connection accepted by instrumented local listeners. There is no online fallback.
8. A strategy calls `subprocess`, shell execution, fork/spawn, threads, native extensions or FFI to evade policy. The OS boundary denies prohibited operations or keeps descendants within the same limits and visibility rules. A child cannot access network, secrets or protected files.
9. A strategy returns an intent that mutates portfolio state, bypasses risk, targets an unconfigured instrument, carries invalid/nonfinite/oversized values, or supplies an unexpected protocol version. Trusted validation rejects it, with no ledger mutation or successful run.

## Resource and lifecycle attacks

10. Run a tight infinite CPU loop, runaway allocation, unbounded recursion, process-tree fork bomb, open-file/handle flood, scratch-file flood and stdout/stderr/output flood separately. Each hits its applicable configured limit within a bounded grace period, ends in an explicit non-success status, and does not exhaust or crash the API/scheduler. A subsequent valid trusted job succeeds.
11. Cancel a sleeping malicious strategy and one with child processes. Confirm all descendants are dead, scratch is cleaned, the job is `cancelled`, and no result is published. Repeat for wall-clock timeout, OOM, worker crash and scheduler shutdown. Verify lease release only after descendant death; a deliberately unkillable/failed-cleanup simulation makes the scheduler unhealthy and blocks submissions.
12. Kill the scheduler abruptly while a strategy is running. Confirm independent lifetime control kills the strategy tree, restart recovers the job as `interrupted`, and no stale process can publish an artifact or run under a reused PID.
13. Concurrently run up to the configured worker cap plus queued jobs. Confirm queue and per-job resource budgets hold under contention; no strategy can change another's state or cross-read its scratch.

## Audit, reproducibility and exit evidence

14. For admission denial, limit hit, crash, cancellation and normal success, assert append-only structured events include UTC time, job ID, source hash, backend/version, budget, stable reason and terminal status. Inject audit-write failure: submission must reject or execution must fail closed. Send a secret-shaped string through exception and stdout to verify redaction and volume limits.
15. Replay a deterministic valid strategy with identical data/source/dependency/seed/backend/budget and compare result fingerprints. Change one hash or backend identity and assert explicit incompatibility instead of silent reuse. Golden accounting and execution fixtures remain byte/economically identical.
16. Run the full lint/type/unit/integration/property suite, the hostile workload suite on each advertised platform/backend, and an independent security review. Capture command, platform, backend version, configured controls, pass/fail/skip counts, process-cleanup observations, audit samples and any unsupported-platform statement in the milestone report. Do not treat skipped attack cases as a passing arbitrary-Python gate.

Acceptance requires all mandatory isolation controls enforced by the OS/backend, all malicious workloads contained, no canary disclosure or protected-file change, reliable process-tree termination and audit, unchanged financial results, and no unresolved critical/high defect. If a backend cannot meet these tests, keep `isolated_python` disabled and report the gate limitation explicitly.
