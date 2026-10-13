# Operator isolated research gateway

The default terminal does not admit submitted Python. Trusted built-in research
remains available. Isolation is enabled only for a process explicitly started
with a trusted local `--isolation-config` file and a compatible operator receipt.
HTTP requests cannot supply approval, image, profile, audit or receipt paths.

## Configuration and receipt

Configuration has exactly `image`, `seccomp_profile`, `audit_path` and
`acceptance_path`. Pin the image by SHA256. The receipt binds engine version,
host identity, every installed Python source hash, dependency lock hash and
freshly probed backend identity. It includes exactly hostile, lifecycle and review
evidence hashes. Each evidence path is a single filename beside the receipt;
directories, absolute paths, traversal, symlinks and junctions reject.

The receipt is a trusted operator assertion over those bytes. Hash matching does
not establish that a report passed or that its evidence covers the current build.
The operator must independently verify that provenance. `acceptance_testing`
receipts identify bounded engineering fixtures and never approve a release.
`isolated_research` requires the completed security and lifecycle acceptance gate.
No final production receipt has been installed by this implementation.

## Public workflow

Start `quant --state-dir STATE --project-root PROJECT --isolation-config CONFIG
serve`. The server binds loopback. `GET /sandbox/capabilities` reports availability,
scope and budget caps. `POST /sandbox/strategies` accepts exactly
`execution_mode: isolated_python`, `source_package` and `request`.

The package contains bounded UTF-8 `source`, its exact `source_sha256` and an empty
`dependencies` list. Requests include instruments, immutable snapshot IDs, UTC
start/end, currency, capital, execution costs, seed and explicit budget. Current
public caps are 5 wall seconds, 3 CPU seconds, 64 MB memory, 4 processes, 32 open
files, zero scratch files/bytes and 4096 output bytes. Invalid or incompatible
requests reject before queue admission. Source remains opaque on the host.

Poll `GET /sandbox/jobs/ID`; read `/events` or cancel with `POST .../cancel` and an
empty JSON object. Successful experiments appear in the shared `/runs` registry.
These jobs have a separate durable queue and cannot enter ordinary trusted routes.
Job views include the source hash and config, never submitted source text.
The CLI equivalent is `quant ... --isolation-config CONFIG sandbox-run --request
REQUEST.json`; it waits for completion and prints the job view as JSON.

## Failure and recovery

One runtime owns containment at a time. Cleanup is mandatory before publication,
terminal job state or ownership release. Readiness loss blocks new submissions;
unconfirmed cleanup retains ownership for explicit recovery. A startup attempt
that never obtained host ownership closes locally without touching another owner.
Queue readiness considers the full queue, independently of history pagination.
Restart reconciles containment before marking running/cancelling jobs interrupted;
interrupted source is not automatically executed again.

Cancellation committed before publication suppresses publication. Publication and
the job decision serialize concurrent cancel writes. Experiment registration and
job commit use separate databases: a process crash between them may retain an
immutable experiment beside an interrupted job. Such an artifact is preserved for
operator recovery, omitted from completed public run listings, and blocked from
detail, report, artifact and comparison access. Public qualification requires a
supported completion schema and a succeeded isolated job whose recorded result
exactly matches the immutable manifest. Missing/corrupt completion metadata rejects.
This does not establish strategy eligibility or final release approval.

No broker connection, order or strategy forward test is performed by this workflow.
