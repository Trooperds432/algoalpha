# M22 isolated runtime integration plan

Reviewed against current source on 2026-10-04 by an independent read-only
architecture reviewer. This plan does not enable public arbitrary-source
execution or pass M16. Existing operator containment evidence remains valid
only for its recorded source, image and profile.

## Required implementation order

1. Add a terminal-owned, bounded WSL keeper beside `sandbox/podman.py`. Its
   fixed guest command only waits. A guardian retains the parent's real Windows
   process handle and creation token, following the existing watchdog identity
   checks. Startup failure, early exit and transport loss block admission. On
   shutdown or parent death, keep transport available until sandbox cleanup is
   proved or its independent maximum lifetime expires; stop only the keeper.
   No systemd/configuration changes, distro termination or global WSL shutdown.
2. Establish exclusive isolation ownership before reconciliation. Initially
   serialize isolated jobs under one coordinator-owned backend. Existing
   `_ACTIVE_LEASES` is process-local, so separate workers sharing an audit path
   incorrectly classify another live worker's pending lease as unhealthy.
   Existing `reconcile()` removes all globally project-labelled containers.
   It must be guarded by exclusive host ownership/quiescence or changed to
   scheduler-scoped labels and leases before public scheduling is wired.
   Cleanup failure keeps ownership and unhealthy state; no lease release or
   terminal job status before confirmed container death/removal.
3. Wire distinct admission through `sandbox/admission.py`, API sandbox routes
   and CLI `sandbox-run`. Trusted operator configuration supplies pinned image,
   profile and audit paths, plus acceptance provenance tied to current source
   and backend. Requests cannot supply those paths or a verified flag.
   Unsupported hosts reject before staging source or claiming jobs. Validate
   bounded source/hash, empty dependencies, zero scratch, budgets, seed,
   instruments and immutable snapshot identities. Keep trusted-route mode
   confusion rejection intact.
4. Integrate the existing `IsolatedStrategy` into a distinct `ResearchService`
   and `ResearchRuntime` execution path. Pass cancellation through to its
   backend. Never import submitted source on the host. Preserve as-of field
   copies, host-side order validation, ordinary risk/accounting and financial
   conventions. Trusted subprocess termination alone is not container cleanup.
5. Require replay compatibility before execution: source, dependencies, data,
   engine, seed, budget and backend identity. Source provenance needs a
   canonical combined archive containing the submitted source, because
   `ExperimentStore._source_hashes()` currently requires strategy bytes inside
   the canonical project archive. Changing only a standalone strategy file
   cannot satisfy that verification.

## Acceptance evidence

Keeper startup, early exit, expiration, graceful close, abrupt parent death,
PID reuse defense, real WSL persistence and no orphan. Public API/CLI rejection
before staging for unsupported hosts, bad hashes/dependencies/scratch/budgets.
One queued isolated backtest through real Podman/as-of/risk/accounting. Queue
capacity, exclusive ownership, cancellation and descendants, timeout, API death,
shutdown, transport loss and audit failure. Restart must not automatically
rerun interrupted source; reconciliation must preserve another active owner's
containers. Same pinned replay must match; incompatible environment must
reject before execution. Then fresh hostile and trusted financial regression
and independent security review against final integrated source hashes.

The written M16 contract requires cleanup before terminal state and lease
release. It does not specify a strict three-second reap deadline. The preserved
test's 3.5-second running-container observation and approximately 10.24-second
completed cleanup audit are distinct observations, neither an invented new
contract nor evidence that public lifecycle integration is complete.
