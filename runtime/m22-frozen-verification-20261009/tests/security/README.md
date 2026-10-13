# M16 hostile container acceptance harness

`test_m16_hostile.py` holds deliberately hostile Python **as strings**. The host
never compiles, evaluates, imports, or executes those strings. Only validated
JSON is delivered to `PodmanBackend.execute`, after explicit operator opt-in and
an effective-control capability probe. The abrupt-client-death host helper
imports the trusted backend and transports the same JSON; it does not execute
strategy source on Windows.

Run only against the disposable M16 image/backend with synthetic data:

```powershell
$env:QT_M16_HOSTILE = 'RUN_IN_VERIFIED_PODMAN'
$env:QT_M16_IMAGE = 'sha256:<verified-local-image-id>'
$env:QT_M16_SECCOMP_PROFILE = '/absolute/linux/vm/profile.json'
.venv\Scripts\python.exe -m pytest tests/security/test_m16_hostile.py -v
```

The seccomp path is a POSIX path inside the Podman Linux VM, not a Windows Path.
The current backend deliberately declares **zero writable scratch bytes/files**:
root and temporary mounts are read-only because a positive inode quota is not
verified. No runtime option may silently substitute positive scratch.

Coverage includes:

- exact benign replay and recorded source/budget provenance; corrupt source hash
  admission rejection;
- generated host environment/file canary, absolute/alternate/traversal paths,
  proc environment/command line, credential paths, write/symlink denial;
- IPv4/IPv6 TCP/UDP socket creation denial, DNS/loopback/host IPC and libc FFI;
- CPU, memory, process descendants, open files, scratch, stdout/stderr, recursion,
  crash and forged response floods;
- nonfinite/negative/zero/bool/oversized intents; host protocol validation;
- cancellation of sleeping strategies and descendants, post-terminal labelled
  container absence, concurrent container state isolation;
- audit redaction and write-failure admission rejection;
- abrupt host client death with a launched descendant workload: parent-death watchdog
  is observed within three seconds with a ten-second conmon budget, **before** trusted reconciliation, followed by recovery cleanup.

Resource attack cases require both launch and cleanup audit events. A rejected
admission is not evidence that an attack was contained. Every terminal test
checks that no M16-labelled container remains; concurrent tests check after
both submissions finish. Audit files use disposable pytest paths. The exact
canary never appears in assertions or console output.

No skipped attack case counts toward a passing M16 gate. Missing opt-in, image,
profile or capability proof produces blocking skips. Capture the real run
command, host/backend/image identity, pass/fail/skip counts and cleanup evidence
in the milestone report. These tests alone do not certify public scheduler/API
admission, risk/ledger enforcement, as-of data isolation, queue limits, immutable
replay compatibility, or trusted financial regressions. Those require the
separate integration/regression suites and an independent security review.

