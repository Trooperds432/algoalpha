# M21 dependency-security and M16 lifecycle review

Date: 2026-10-02. Scope: M16 Podman lifecycle finding and current host proof;
dependency audit is coordinated separately by the root agent. This review does
not pass M16 or certify public arbitrary-source execution.

## Preserved finding and focused correction

The independent review in `m16-independent-review.md` identified a high
unresolved watchdog-lifecycle finding from the supplemental parent-death audit:
`watchdog_parent_death` was followed approximately five seconds later by
`watchdog_failure`, with no cleanup or termination event before reconciliation.
The preserved trace is
`m16-hostile-run-20261001C-parentdeath-audit.jsonl`. The old hostile fixture
proved the strategy container was no longer running within three seconds, then
immediately called owner reconciliation. It did not establish that the detached
watchdog had completed container removal and durable audit first. That allowed
reconciliation to race and obscure watchdog cleanup state.

The watchdog's container-removal and post-removal listing subprocess timeouts
were five seconds, matching the delay in the trace. They are now 15 seconds.
The strategy's parent-death stop assertion remains bounded at three seconds.
The hostile fixture now observes parent-death, cleanup, and termination audit
events, rejects watchdog-failure, and checks that the lease and unhealthy marker
are clear before recovery reconciliation. The audit wait is bounded at 45
seconds. This is a focused source/test correction; it still requires an actual
hostile container rerun before the finding can be considered resolved.

## Current host and runtime evidence

The existing dedicated WSL distro is `podman-algoalpha-m16`; no distro, image,
or machine was created or deleted. `podman machine start algoalpha-m16` returned
`machine did not transition into running state: ssh error: machine not in
running state`. `podman machine list` reports the machine stopped, despite
`wsl --list --verbose` showing the distro running. The Podman connection now
references host port 50829, while Windows Podman cannot reach its configured
remote endpoint. The focused documented hostile test was attempted with the
preserved image and seccomp path, but capability preflight blocked it:

```text
SKIPPED: M16 blocked: effective-control probe podman_command_failed
```

This skip is blocking evidence, not a pass. Direct in-distro, read-only checks
showed Podman 5.8.8, rootless operation, seccomp enabled, cgroup v2 with CPU,
memory and pids controllers, the preserved image digest
`sha256:083a4ee8f04d4e4c91bc735931eccd5780c8e4e8ba7acfae04b6234fc573fc29`,
the existing `/home/user/algoalpha-seccomp.json` profile, and zero containers
with the M16 owner label. These checks do not establish that the Windows
supervisor can enforce cleanup or that a hostile test ran.

The preserved 2026-10-01C report recorded 30 passed, zero failed, zero skipped,
but also explicitly marked `milestone_gate: not_passed`; its later supplemental
parent-death trace is the unresolved failure above. Neither result is upgraded
by the current work.

## Fresh focused verification

- `.venv/Scripts/python.exe -m pytest tests/unit/test_sandbox_podman.py tests/unit/test_sandbox_protocol.py tests/unit/test_sandbox_worker.py -q`: 44 passed.
- Ruff on `src/quant_terminal/sandbox/podman.py` and `tests/security/test_m16_hostile.py`: passed.
- Strict mypy on those two files: passed.
- Actual `tests/security/test_m16_hostile.py::test_abrupt_client_death_reaps_within_three_seconds_and_recovers`: blocked at effective-control preflight as recorded above; no hostile test ran.

Final edited source hashes:

- `src/quant_terminal/sandbox/podman.py`: `c1fdf43b465799b077ee008ca7ee53c60d62f1e6ce29904d7591a03a6a3c3acf`
- `tests/security/test_m16_hostile.py`: `766fdb7f6d0e34fd94701928fcf51faa0a0a04c838b5c20b9621a6ba77aa81de`

## Remaining gate blocker

Restore the existing machine's Windows-to-WSL Podman remote connection without
changing system-wide WSL settings. Then rerun the documented hostile suite on
the preserved dedicated image/profile, retaining its full audit log. In
particular, independently verify that the watchdog records parent-death,
cleanup, and termination; that no watchdog-failure event occurs; that the lease
is gone and no M16-labelled container remains before reconciliation; and that
strategy stop remains within three seconds. Run the integrated trusted
regression suite and obtain the pending independent review afterward. Until
those results exist, M16 remains **not passed**, and arbitrary-source admission
must remain disabled.
