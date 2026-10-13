# ADR 0003 — canonical experiment records and local artifact storage

Status: accepted at the M8 gate on 2026-09-27.

## Context

M8 must make completed backtests searchable and traceable to exact source,
dataset, parameters, assumptions, seed, environment and economic output. The
terminal is currently a local single-owner modular monolith. The architecture
baseline selects SQLite metadata and immutable local artifacts; PostgreSQL and
multi-user coordination are outside the current deployment.

## Decision

Represent each run with a deeply immutable, versioned manifest whose canonical
JSON produces an input fingerprint. Hash the economic result projection using
exact Decimal text and UTC timestamps. Store metadata and append-only tag
events in SQLite, and store source/report/result artifacts as hash-verified
bytes in a per-run directory. Stage and verify the complete directory before
an atomic rename, then insert the metadata in a serialized SQLite transaction.
On a crash between rename and commit, the unregistered artifact directory is
an orphan ignored by search and handled only by explicit recovery. Registered
manifests/artifacts are never overwritten. Reads and reruns verify manifest and
artifact hashes.

Rerun requires an explicit trusted application adapter receiving the immutable
manifest, verified artifacts and configured DatasetStore. It returns a new
BacktestResult, which is recorded with a new run ID and compared by exact
economic fingerprint. Stored Python source is retained as data and is never
automatically imported or executed. Since M5 currently has deterministic
strategies and no RNG argument, M8's repeat gate covers deterministic runs;
stochastic adapters remain unsupported until the engine accepts the recorded
seed explicitly.

## Alternatives

- PostgreSQL metadata now: deferred because the application is local-first and
  has no server lifecycle or deployment requirement.
- Filesystem-only manifest scanning: rejected because indexed experiment
  search, atomic metadata transactions and appended tag history are clearer in
  the already-selected SQLite architecture.
- Execute source stored in a manifest to rerun: rejected because source is
  untrusted data and M16 isolation is not implemented.
- Tolerant economic equality: rejected for exact Decimal ledger outputs; M8
  identifies any changed economic value as a different result.

## Consequences

SQLite serializes local writers and gives deterministic searchable metadata.
Artifacts remain portable, inspectable files with content hashes. Source
retention and input identities improve future reproduction, while actual
strategy execution remains an explicit trusted caller responsibility. M8 does
not promise multi-user/network-filesystem safety or stochastic replay.
