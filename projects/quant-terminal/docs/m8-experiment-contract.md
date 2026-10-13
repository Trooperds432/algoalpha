# M8 experiment management and reproducibility contract

## Objective and scope

Turn completed research runs into immutable, searchable experiments. An
experiment is identified by a stable run ID and records enough provenance to
trace the result to strategy source, application source/revision, data
snapshots, configuration, execution assumptions, environment, and seed.
The registry is local filesystem storage for trusted owner-controlled
workspaces. It is not a multi-user database, execution sandbox, or broker
service. It never executes strategy code while searching, comparing, cloning,
or verifying a stored run.

## Manifest and input identity

`ExperimentManifest` is a frozen, versioned record. It contains:

- run ID, creation time, strategy identifier/name, and SHA-256 of strategy
  source;
- project source hash, Git commit (nullable only when the checkout has no
  commit), package/engine version, Python version, and dependency lock hash;
- ordered dataset snapshot IDs, content/input hashes, row counts, and
  instrument identities;
- start/end, currency, initial capital, strategy parameters, explicit seed,
  backtest settings, analytics settings, benchmark, and execution assumptions;
- deterministic input fingerprint, canonical economic-result fingerprint,
  typed scalar metrics, tags and artifact descriptors.

All exact numeric inputs/results are canonical Decimal strings; timestamps are
timezone-aware UTC ISO-8601. JSON is sorted-key UTF-8 with no NaN/Infinity. The
input fingerprint hashes canonical strategy identity/source, project identity,
dataset identities, parameters, seed, run interval, capital, and all execution
and analytic settings plus the Python version; it excludes run ID, creation
time, machine/host metadata, tags and artifact paths. The
dependency lock hash and engine version are also part of the configuration
identity. A missing Git commit is explicit `null`, never a fabricated
revision.

Each record retains a verified `source-bundle.zip` containing the project
source snapshot and `strategy-source.py` containing the exact strategy module
bytes; both are hashed artifacts, not executable payloads. The project source
hash equals the source-bundle hash and the strategy source hash equals the
strategy artifact hash. Verified artifact reads let an owner retrieve those
bytes later. Dataset rows reference immutable M2 snapshot IDs and hashes; the
rerun adapter resolves them in a configured `DatasetStore` and verifies IDs
and content hashes before constructing a backtest.

`build_source_bundle(root)` defines the project archive bytes: walk regular
files while excluding `.git`, `.venv`, runtime caches, build outputs, `tmp`,
and the changing `coordination/` and `docs/validation/` evidence directories; reject the root or any ancestor/nested symlink or junction; normalize member names to sorted relative
POSIX paths; use ZIP_STORED (no compression), fixed 1980-01-01 timestamps,
fixed regular-file permissions, empty extra/comment fields, and no archive
comment. This makes a source tree's archive hash independent of filesystem
timestamps, traversal order, host path separator and compressor version.

The economic-result fingerprint hashes a canonical projection of deterministic
economic outputs: NAV checkpoints and positions, orders and ordered lifecycle
events, fills, benchmark observations, and costs. It excludes run ID, created
time, machine/runtime metadata and report presentation. A repeated run with the
same input fingerprint passes only when this result fingerprint is byte-exact;
M8 uses no tolerance for Decimal ledger values. A mismatch reports failure and
does not overwrite either run. Random seed is mandatory unsigned 64-bit input,
even for deterministic strategies, and must be passed through unchanged by
future stochastic runners.

## Registry and artifacts

`ExperimentStore.create` requires a trusted capture adapter to supply a typed
`ReplayOutcome` for the result just produced. This outcome binds the strategy
and project hashes, exact resolved M2 dataset identities, and manifest input
fingerprint to the `BacktestResult`; the store independently checks each
against the manifest. It also checks M5's immutable result metadata for
strategy name, dataset snapshot IDs, currency, interval, initial cash,
initial NAV checkpoint, shorting and zero-cost permissions, max order notional,
participation, commission/slippage model representations, zero-cost label,
and benchmark. A result generated with a
different declared configuration is rejected before storage.

`ExperimentStore` keeps immutable run artifacts in one directory per run and
indexes manifest metadata in SQLite, consistent with the local deployment
architecture. Creation writes a private staging directory and canonical
manifest plus caller-supplied named artifact bytes, verifies hashes, then
atomically renames the complete directory into place before inserting its row
and artifact descriptors in one `BEGIN IMMEDIATE` transaction. A crash between
the directory rename and SQLite commit can leave an unregistered orphan, which
search ignores and explicit recovery can report/remove; SQLite must never point
to a missing/incomplete artifact directory. Existing run IDs are rejected.
Artifact names are restricted to safe single path components, and no symlinks
or traversal are followed. Crashed staging directories are never mistaken for
complete runs and can be removed by an explicit recovery operation after
validation. SQLite serializes writers; transactions fail closed and must not
silently merge partial artifacts. Schema version and indexes are explicit.

Search operates on indexed manifest fields and append-only SQLite tag events,
with stable ordering by creation time then run ID. Comparison joins named
metrics and preserves unavailable values/reasons; it does not rank runs or
declare a winner. Configuration cloning returns a detached copy and creates
no run until the caller submits it. Rerun verification receives an explicit
caller-provided `runner(manifest, verified_artifact_directory, dataset_store)`
adapter. It verifies that the selected strategy module bytes hash to the
manifest's strategy source hash and came from the retained project bundle,
reconstructs a new `BacktestEngine` from manifest settings and verified M2
snapshots, and returns a typed `ReplayOutcome` containing the result, strategy
and project source hashes, exact dataset identities, and input fingerprint.
The registry rejects an outcome with any identity mismatch before comparing
the new result fingerprint and recording it under a new run ID. Stored
manifests and source archives are data and never trigger imports, subprocesses,
or network access. Current M5 strategies are deterministic and M5 accepts no
seed argument; M8 verifies repeatability on deterministic M5 strategies while
retaining a mandatory seed as provenance. Future stochastic runners must pass
the recorded seed through an explicit engine RNG interface before claiming
reproducibility. Reruns always create a new run ID and retain both outcomes.
The M7 report bundle is regenerated from each rerun result. Other non-source
artifacts are explicitly listed as omitted in rerun metadata rather than being
copied under a different result fingerprint; their originals remain attached
to the original run.

Every `get`, artifact-read, and replay verifies canonical SQLite manifest
bytes, stored manifest SHA-256, and artifact size/hash before returning
content. Corruption raises an integrity error and never repairs a registered
run by overwriting it. SQLite `BEGIN IMMEDIATE` serializes writers with a
bounded busy timeout; database triggers reject manifest/artifact updates or
deletes and tag-event updates or deletes; a busy store raises a typed busy error. A pre-existing
unregistered run directory is an orphan conflict and is never replaced.
Recovery reports staging/orphan directories and removes only an explicitly
selected unregistered path after containment and symlink checks; registered
paths are immutable. Artifact source inputs are bytes, not paths, so the store
never follows caller-supplied source symlinks.

## Required tests and gate

Golden tests cover canonical fingerprints, changed-input sensitivity,
identical-run equality and changed-result mismatch; mandatory seed and UTC
validation; nested manifest immutability; exact source bytes and artifact
hashes; same-tree canonical source bundles; duplicate run rejection; traversal
rejection; symlinked store root/run/artifact/staging path rejection with no
outside read/write; failed/crashed staging visibility and recovery;
registered-artifact tamper detection; incorrect replay code/data/input identity
rejection; same-ID concurrent writer collision/busy handling without
replacement; append-only tags; filtered deterministic search; metric
comparison including unavailable values; detached configuration clone; rerun
creating a new immutable record; mismatched-rerun report regeneration; initial
result-to-manifest provenance rejection; and deterministic integration with an
actual M5-produced `BacktestResult` and M7 report artifacts. Runtime environment
and machine capture is informational and documented as such.

The exit gate is that an owner can trace a stored result to code, data and
configuration and reproduce its exact economic output from those identities.
M8 does not add arbitrary strategy loading or process isolation; M16 addresses
hostile strategy containment. It performs research management only and does
not submit paper/live orders.
