# ADR-004: Shared local application service and durable process jobs

Status: Accepted for M13. Date: 2026-09-28.

## Context

The Python research engines are reusable, but a graphical terminal requires one
headless contract, durable campaign status, and execution that cannot block HTTP.
The workstation application serves only its loopback interface. Long jobs can run
longer than one request and users need clear recovery after cancellation or crash.

## Decision

The CLI, local HTTP API and future UI call one Python `ResearchService`. FastAPI
submits bounded job requests to a SQLite event journal. One OS-held scheduler lease
owns a state directory and starts bounded trusted child processes with an
allowlisted environment. Jobs and per-trial progress have stable generated IDs.
Crash recovery marks running work interrupted; it never labels a partial job as
successful or silently resumes from uncertain state. Worker liveness watches the
scheduler, and its lease remains held until worker death is confirmed. Broker
capabilities are absent from this research service.

## Alternatives

An in-process FastAPI background callback is simpler but shares memory/failure
boundaries with request handling. PostgreSQL plus a distributed queue adds
deployment complexity to the local-first workstation use. Pickle/job objects
persist executable code and are unnecessary; request JSON is canonical data.

## Consequences

SQLite supports one local scheduler per runtime root. CLI execution against a root
owned by a running server rejects; the API remains the owner until shutdown. A
cancelled job can leave valid immutable M8 runs from completed substeps, but its
parent outcome is not a passed campaign. Child processes bound failure propagation
but do not create the resource-secure M16 strategy sandbox. The future UI must use
the same API and display all persistent job states.
