# M22 host ownership and containment retest

Date: 2026-10-04. Source and tests were frozen before the live hostile retest.

## Host ownership change

The Podman backend now takes one per-user cross-process OS lock around
capability probing, isolated execution and destructive reconciliation. It
rejects contention before issuing Podman operations and releases ownership on
process death. On Windows, opened directory handles are pinned against rename
or deletion, native reparse points are rejected, the opened leaf handle is
validated, and the sandbox directories and lock leaf owner/DACL are checked.
POSIX uses descriptor-relative `O_NOFOLLOW` opens and validates owner, mode,
file type and link count. This protects the trusted worker boundary under the
assumption that host code under the same user, SYSTEM/Administrators and the
Windows profile parent are trusted. It does not defend against a host-account
compromise or administrator.

Fresh Windows checks: focused sandbox suite **58 passed, 3 skipped** (one
unavailable symlink privilege and two POSIX-only cases); Ruff and strict mypy
passed. The independent read-only review matched the frozen hashes, reran the
lock-focused selector (**10 passed, 1 skipped, 22 deselected**) and found no
substantiated blocking defect. It did not run the Podman suite.

## Current-host hostile suite

After a fresh effective-control probe and empty owner-label inventory, the
guarded suite ran against pinned image
`sha256:083a4ee8f04d4e4c91bc735931eccd5780c8e4e8ba7acfae04b6234fc573fc29`
with `/home/user/algoalpha-seccomp.json`. The clean authoritative run completed
**30 passed, 0 failed, 0 skipped** in 176.25 seconds. Its JUnit report records
all cases, including container-network and host-path denial, resource/protocol
bounds, audit failure/redaction, cancellation and descendants, serialized
competing attempts, and abrupt-client-death recovery. The post-run
`podman ps --all --filter label=algoalpha.owner=quant-terminal-m16 --format
json` inventory was empty. An earlier complete run also passed 30/30; a brief
interrupted duplicate left a probe container, which was removed by its exact
owner/job labels and followed by an empty inventory before the clean rerun.

The tests show operator-triggered containment for these fixtures and hashes on
this host. They do not show durable unattended WSL lifetime or the public
isolated research-job lifecycle. The parent-death assertion's timing tolerance
and cleanup/audit completion timing remain qualified as in
`m22-security-independent-review.md`; no strict three-second cleanup claim is
made.

## Frozen evidence hashes

* `src/quant_terminal/sandbox/podman.py`:
  `28048c922f1ffea859255c2aeac970c12ebc0cccdb4c251aeb7269420abab355`
* `src/quant_terminal/sandbox/protocol.py`:
  `caa9342eb746f6eb38ff12bc7d4ad751310a415a573228195d1037c4c7c86394`
* `src/quant_terminal/sandbox/strategy_worker.py`:
  `0e5db5c41d1ad49df2cf87e5f4d58d66128538b483490d40107d477238cc94b`
* `tests/unit/test_sandbox_podman.py`:
  `6177e81178e47723ef7d92f415bb9946fe88fdfaf69055d7436ef51f4cc5ca2e`
* `tests/security/test_m16_hostile.py`:
  `677aafe336cbf9a1ddb32a6fdbc6685ec11e425e7bf8d433ea3b97f3ee7bff10`
* JUnit: `m22-hostile-run-20261004D.xml`, SHA-256
  `0f834332293930ffe26f530261b554c73813c41aad73adb396dc1f6cdde8f44b`.
* Full check: `checks-20261004T184541751301Z.json`; **669 passed, 35
  skipped**, all check commands exit zero. Skips include the opt-in hostile
  suite and platform-specific symlink/permission tests; the separate live suite
  above is the hostile acceptance evidence.

## Gate decision

This is fresh M16 backend containment evidence, not an M16 pass or release
approval. A terminal-owned bounded WSL guardian, actual public admission and
scheduling integration, cancellation/cleanup/restart/replay acceptance,
final installed package provenance, and complete independently reviewed
370-role participation remain outstanding. Public arbitrary-source execution
stays disabled. M16, M21 and M22 remain in progress.
