# Actual graceful isolated API shutdown — ALG486

Issue `4381cd24-9748-4790-8121-0a9149a0ff78`, 2026-10-09. Scope is the new bounded
acceptance harness and its evidence only; no production changes or gate status edits.

`coordination/m22_check_graceful_shutdown.py` starts a disposable actual loopback
Uvicorn API child with the existing current acceptance-testing operator receipt.
An opaque, unique source requests a 60-second sleep inside Podman. The parent sees
its source-specific launch audit and directly inspects its container as Running.
Only then a trusted stdin command sets `uvicorn.Server.should_exit=True`, exercising
normal server shutdown and the actual ASGI application lifespan. This does not
test Windows OS signal delivery. No forced kill or termination fallback can establish
a passing result; incomplete normal cleanup would remain a failed report.

Command: `.venv/Scripts/python.exe coordination/m22_check_graceful_shutdown.py
docs/validation/m22-real-gateway-20261008T195348Z.json`, exit 0. Evidence:
`m22-graceful-shutdown-20261009T122527Z.json`. Harness SHA256:
`80522f56545a5ec42e3b7e102db3c2ef09cade275623bb57be3a9409b56f4128`.

Observed child PID40780 exited naturally with code0. Job
`936e8e4d2cfb4c51a16e8cebee69ba85` became interrupted with a null result and no
registered experiment. There was exactly one source launch; its termination reason
was cancelled. Container cleanup at12:25:58.283295UTC preceded the terminal job
timestamp12:25:59.286180UTC. The child returned from normal server shutdown at
12:25:59.302062UTC with no gateway/scheduler failure, no active source, no retained
runtime owner or scheduler lease, and no surviving gateway thread. Empty inventory
was observed before independently reacquiring both the scheduler lease and host OS
ownership. Final inventory was also empty. The private child log/outcome paths are
recorded in the JSON report. This demonstrates ordering and this bounded shutdown
path, not a universal timing deadline or every possible OS/transport failure.

The writer reconciled all104 source hashes against current files. Both current
root-authored keeper-loss and timeout reports (`m22-gateway-transport-loss-20261009T121549Z.json`
and `m22-gateway-timeout-20261009T121752Z.json`) also have104/104 matching source
hashes. These close the earlier current-source gateway lifecycle gaps. Scoped Ruff
and formatting checks pass. Initial pre-format lint found11 line-length errors;
normal formatting corrected them, and fresh checks passed. No failed actual runtime
attempt occurred in this slice.

Whole-M16 approval remains a coordinator decision following independent evidence
review. A separate consolidated audit observation remains open: keeper-transport-loss
revokes readiness and clears backend identity, so its termination event has version
null and empty image_provenance even though earlier admission/launch entries record
the verified backend. This is a forensic metadata requirement question, not observed
escape, secret exposure or unsafe publication; the coordinator was informed and no
production repair was made here. The complete370-role participation and final
release gates remain separate and unapproved.
