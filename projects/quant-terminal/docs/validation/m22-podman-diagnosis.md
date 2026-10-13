# M22 dedicated Podman connection diagnosis

Date: 2026-10-03. Target: existing `algoalpha-m16` machine and
`podman-algoalpha-m16` WSL distro. No strategy source, image, security profile,
machine definition, SSH connection, or global WSL configuration was edited.
The later authorised recovery experiment changed only guest `/etc/wsl.conf`,
preserved a backup, and restored the original bytes when enforcement failed.

## Direct observations

Initial inventory found both the machine and distro stopped, WslService running,
and no listener on configured loopback SSH port 50829. Windows Podman is 5.8.3;
the preserved guest engine is 5.8.8.

`podman --log-level debug machine start algoalpha-m16` booted the existing distro,
started SSH and API forwarding, then exited 1 after approximately 22 seconds:
`machine did not transition into running state: ssh error: machine not in running state`.
Immediately afterward, Windows `podman info --format json` succeeded over the
existing default rootless connection. SSH was active on port 50829, both Windows
loopback listeners existed, and the guest user Podman socket was present.

Guest `/etc/wsl.conf` has `[boot] systemd=true`. Guest PID 1's command is
`/sbin/init`, resolving to `/usr/lib/systemd/systemd`. The exact provider command
`ps -eo cmd,pid | grep -m 1 ^/lib/systemd/systemd` produced no match. Machine
metadata nevertheless reported `Running: false` while remote API requests worked.
The sole failed system unit was systemd-binfmt; SSH and rootless socket activation
worked. Podman service idle termination returned Result=success, which by itself
is normal socket activation behavior.

With no foreground WSL client, the connection subsequently disappeared. The first
exact hostile-suite attempt was preserved as `m22-hostile-run-20261003.txt` and
XML: all 30 tests skipped at effective-control preflight (`podman_command_failed`).
That is a blocked attempt, never containment evidence.

## Cause and narrow recovery

The machine-state false negative is confirmed by matching the guest process
listing to the Windows provider's exact detector. Podman 5.8.3 expects its own
nested systemd bootstrap; its detector matches a command beginning
`/lib/systemd/systemd`, which native WSL PID 1 does not expose. See the
[version-pinned provider constants](https://raw.githubusercontent.com/containers/podman/v5.8.3/pkg/machine/wsl/declares.go)
and [provider implementation](https://raw.githubusercontent.com/containers/podman/v5.8.3/pkg/machine/wsl/machine.go).
The [upstream native-systemd issue](https://github.com/podman-container-tools/podman/issues/29356)
reports the same failure pattern. The observed loss of SSH after foreground WSL
clients exit is an additional availability limitation; it is not a seccomp failure.

A bounded trusted foreground command, `wsl -d podman-algoalpha-m16 -u user -- sleep 900`,
kept this existing distro alive without altering its security configuration.
Fresh Windows API checks then reported rootless=true, seccomp=true, cgroup v2,
and engine 5.8.8. This temporary operator keepalive permits direct containment
testing; it does not repair machine-state reporting or establish unattended service
availability. It is not a process with a spoofed systemd name.

For durable recovery, after all test jobs finish and owner-labelled containers are
absent, preserve the guest configuration and remove only the dedicated guest's
native `[boot] systemd=true` setting, retaining `[user] default=user`. Terminate
only `podman-algoalpha-m16`, then start the existing `algoalpha-m16` machine.
Recheck metadata, SSH, rootless API, effective controls, and persistence after
clients exit before accepting that recovery. This configuration change has not
been performed here. Do not repeatedly start a healthy remote API merely because
machine metadata says stopped; upstream reports warn that this can reassign SSH
ports. No global `wsl --shutdown`, distro deletion, new VM, or SSH-key replacement
is required by the present evidence. The subsequent attempt below demonstrates
that this suggested native-systemd removal does not preserve enforcement here.

## Hostile rerun

The second attempt uses unchanged guarded fixtures, the preserved image
`sha256:083a4ee8f04d4e4c91bc735931eccd5780c8e4e8ba7acfae04b6234fc573fc29`,
and `/home/user/algoalpha-seccomp.json`, with the explicit operator opt-in.
Its output is `m22-hostile-run-20261003B.txt` and XML: **30 passed, zero failures,
zero skips, 181.30 seconds**, exit 0. The fixtures actually launched guarded
containers, including concurrent jobs, cancellation with descendants, network,
filesystem, resource, protocol, and abrupt client-death cases. Neither machine
boot nor a successful Podman info call passes M16.

The retained `m22-hostile-parentdeath-audit-20261003B.jsonl` records
`watchdog_parent_death` at 11:52:08.446189Z, `cleanup` at 11:52:18.682097Z,
then `termination` at 11:52:18.685080Z, with no `watchdog_failure`.
The updated fixture requires cleanup and termination, no pending lease and no
unhealthy marker **before** reconciliation. That fixture passed. Cleanup audit
completion took approximately 10.24 seconds after the parent-death audit event;
it is distinct from the running-container stop deadline. The stop polling test
uses a three-second polling window but permits a final elapsed assertion of
3.5 seconds; no raw stop timestamp was recorded, so this report does not claim
an independently measured strict 3.000-second stop guarantee.

The shared run audit is retained as `m22-hostile-audit-20261003B.jsonl`.
Post-suite `podman ps --all --filter label=algoalpha.owner=quant-terminal-m16
--format json` returned `[]`. Hostile source remained inside the pinned image;
the trusted client subprocess only transported JSON to the backend.

Source SHA-256 values checked during the run:

- `sandbox/podman.py`: `c1fdf43b465799b077ee008ca7ee53c60d62f1e6ce29904d7591a03a6a3c3acf`
- `sandbox/protocol.py`: `caa9342eb746f6eb38ff12bc7d4ad751310a415a573228195d1037c4c7c86394`
- `sandbox/strategy_worker.py`: `0e5dbe5c41d1ad49df2cf87e5f4d58d66128538b483490d40107d477238cc94b`
- `tests/security/test_m16_hostile.py`: `766fdb7f6d0e34fd94701928fcf51faa0a0a04c838b5c20b9621a6ba77aa81de`

**Verdict: operator containment evidence passes this preserved suite; complete
M16 and final release acceptance remain partial.** The independent review must
evaluate the lifecycle evidence, stop-bound precision, unattended VM availability,
and missing public isolated scheduling/restart/replay integration separately.
Arbitrary-source public admission remains disabled. The foreground keepalive
is bounded to 900 seconds and is not a production service.

## Authorised durable recovery attempt and rollback

After the suite, the coordinator authorised a narrow dedicated-guest configuration
experiment. Owner-labelled containers were absent. The original `/etc/wsl.conf`
bytes were preserved exclusively at `/etc/wsl.conf.m22-native-systemd-20261003.bak`.
The trial configuration contained only `[user] default=user`, matching Podman's
version-pinned bootstrap baseline. Only `podman-algoalpha-m16` was terminated.
`podman machine start algoalpha-m16` then exited 0 and metadata correctly reported
running. Its nested systemd appeared as `/lib/systemd/systemd`, PID 16.

However, Windows `podman info` reported rootless=true and seccomp=true with cgroup
v2 but **an empty controller list**. The unchanged backend's fresh effective-control
preflight returned `available=False, reason=cgroup_limits_unverified` (audit retained
in `m22-durable-probe-audit.jsonl`). No hostile workload was run in this mode.
Correct metadata does not justify weakening CPU, memory, or process enforcement.

The original configuration was immediately restored byte-for-byte from its backup,
and only the dedicated quiescent distro was terminated. A new bounded trusted
foreground `sleep 900` resumed the native-systemd guest. Fresh unchanged backend
preflight returned `available=True, reason=controls_verified, engine_version=5.8.8`,
retained in `m22-native-restored-probe-audit.jsonl`; owner-labelled containers again
returned `[]`. The backup remains available; no global WSL setting was changed.

**The nested-systemd recovery is rejected on this host.** Retain native systemd for
enforcement and implement a bounded terminal-lifetime keeper if unattended
availability is required: a trusted foreground WSL process owned by the terminal,
with an explicit maximum lifetime, health checked before isolated admission, and
stopped on terminal shutdown or death. Its only guest action should be waiting;
it must not masquerade as systemd, alter limits, or treat machine metadata as proof
of controls. Admission must still use the effective backend probe and reject when
the keeper or transport fails. Implementation requires a separate source owner and
fresh keeper shutdown, parent-death, transport-loss, and hostile integration checks.
