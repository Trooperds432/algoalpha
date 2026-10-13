# M22 independent security retest review

Date: 2026-10-03. Independent reviewer examined the preserved M16 hostile
acceptance run `docs/validation/m22-hostile-run-20261003B.xml` and corresponding
audit logs. The run reports **30 passed, 0 failed, 0 skipped** in 181.30 seconds.
Its cases cover benign replay/provenance, host file/environment/process/write
denials, socket/DNS/loopback/IPC/FFI denial, CPU/memory/process/open-file/scratch
and output bounds, malformed intent rejection, cancellation and descendants,
private concurrent container state, audit redaction/fail-closed behavior,
source hash rejection, abrupt client death, and child-process inheritance.

The evidence supports operator-triggered hostile containment on the tested
machine. The abrupt-death test passes its live assertion that the container is
no longer running inside a 3.5-second observation tolerance, earlier than the
configured 10-second conmon timeout. Archived timestamps separately show parent
death at `11:52:08.446Z`, cleanup at `11:52:18.682Z`, and termination/audit at
`11:52:18.685Z`. Those timestamps establish completion of cleanup/audit about
10.24 seconds after parent-death observation; they do not establish a strict
3.000-second kill/reap duration. Keep that qualification explicit.

Independent reviewer reports source-hash verification against the evidence.
The review does **not** establish durable unattended WSL runtime support or the
public isolated-job admission, scheduling/queue, lease/restart, replay and
backend-incompatibility controls. Public arbitrary strategy-source admission
remains disabled. These are unresolved M16/M21/M22 release blockers. The hostile
test result alone does not pass M16 or authorize a release.
