# M22 internal runtime integration — 2026-10-07

M16, M21 and M22 remain in progress. This report records prerequisites, not final
release approval. Public `isolated_python` admission remains disabled and there is
no broker connection, order or strategy forward test.

## Retained runtime ownership

ALG-443 was reassigned to the coordinator after the actual Infrastructure Engineer
run failed with an ACP terminal limit. Its implementation contribution remains
attributed, but it does not count as completed employee participation. The resumed
writer was explicitly stopped before coordinator source integration.

Independent review found that an arbitrary guardian exit could be mistaken for
cleanup proof. The keeper now accepts only the recorded clean/handled-failure/
reaped-expiry outcomes; an unknown exit or failed audit retains unhealthy ownership.
Fresh focused checks passed 76 tests with three platform skips. Independent review
repeated them. Integrated evidence before the later runtime/source work is
`checks-20261006T204131023945Z.json`: 715 passed, 35 skipped, all other checks passed.
Those skips are not hostile-workload acceptance.

The first actual owned-runtime attempt failed at native ownership ACL validation:
`m22-owned-runtime-20261006T204047Z.json`. The exact private namespace contained
only its sandbox directory and ownership lock, with no reparse points. The
coordinator backed up security descriptors in
`C:/Users/Jonat/algoalpha/runtime/m22-ownership-acl-before-20261006T204239Z.json`
and protected only `.algoalpha`, `.algoalpha/sandbox` and `podman-owner.lock` for
the current user, SYSTEM and Administrators. No broad group write permission was
accepted by weakening code, and no recursive/global filesystem change was made.

`m22-owned-runtime-20261006T204315Z.json` records the passing actual Windows/WSL
check: retained ownership, effective Podman probe, competing backend rejected,
two equal benign contained protocol responses, cleanup confirmed before release,
and an empty post-owner project-container inventory. Exact source, image, worker,
protocol, seccomp and engine identities are in that receipt. The benign call is a
protocol fixture, not a queued backtest. Later Podman pinning edits require fresh
runtime acceptance against their new source hash.

## Opaque package and canonical source archive

Actual Paperclip Software Engineer ALG-450, run
`d60cbb75-58bc-46b0-b93c-f5c767572dc6`, implemented `SourcePackage` and its tests.
Both direct construction and exact JSON-object parsing enforce nonempty UTF-8,
512,000 encoded bytes, lowercase matching SHA256 and an empty dependency array.
Unknown fields/paths/verified flags reject. Source is never compiled or executed.
The successful run and its role HTTP receipts must be independently verified;
the contribution is not accepted merely because the issue is done.

Coordinator ALG-451 added optional submitted-source bytes to the canonical project
archive at the fixed reserved `submitted-strategy.py` entry. Case-insensitive
collisions reject, bytes remain in memory, canonical metadata is preserved and the
ordinary trusted bundle remains unchanged. Clean pre-implementation failures are
retained in `m22-source-bundle-red-20261006.txt` (eight failures). An earlier huge
parameter ID caused fixture errors/noisy output; explicit short IDs corrected that
test presentation without changing its boundary.

Coordinator checks: 30 passed, two Windows symlink skips, Ruff/format/mypy passed.
Independent reviewer checks: 13 passed, one Windows symlink skip, Ruff/mypy passed.
The reviewer directly exercised `ExperimentStore._source_hashes`: the combined
archive passed and a changed strategy artifact rejected. Reviewed source hash
`96e0f0a88ee61b10a6bedf93824bf32e20c72d2fa519bf11a0172bb71e4213ce`;
test hash `ce7cd809b8d781bde3227c2edcc6189b9b89311ca18e2924fdeebee87786160d`.

## Operator-only research runtime

Coordinator ALG-453 extracted the common financial/data configuration parser and
added a distinct internal `IsolatedResearchRuntime`. The operator supplies the
source package and backend. Source/hash, empty dependencies, zero scratch, integer
budgets, CPU/wall relation, seed, history, configured instruments, pinned datasets,
engine, dependency lock and backend identity are validated. The ordinary as-of
bridge, host proposal validation, risk, accounting, analytics and registry remain
in use. Cancellation is forwarded to the backend. Submitted source is included in
both the standalone strategy artifact and the canonical combined archive.

The initial missing-module test is retained in
`m22-isolated-runtime-red-20261006.txt`. Coordinator integration checks passed 23
tests. Source/protocol/bridge/runtime checks passed 69 tests. Two attempted test
commands named nonexistent unit bridge files and ran no tests; the corrected
command used `tests/integration/test_m16_strategy_bridge.py`.

Independent review found a replay compatibility blocker: identity could change
after strategy construction and be detected only after an observation executed.
The coordinator bound the expected identity to each strategy wrapper and checks it
before and after each call. Podman receives that expected identity and compares it
after its fresh effective probe, while holding exclusive ownership, before any
submitted-source lease/container is created. Two regression checks cover that
boundary. The Strategy protocol now describes its name as read-only for consumers,
matching the frozen isolated bridge; the engine never mutates strategy names.
After this fix, 44 tests passed with three platform skips and mypy passed two files.
Final independent retest passed 139 tests with three platform skips, Ruff and mypy
for six source files. It independently checked incompatible backend/engine/source
rejection before further execution, and cancellation after a worker response before
registration. No blocker remains in this bounded internal slice. Fresh integrated
`checks-20261007T155209050628Z.json` passed 781 tests, 35 skipped, with Ruff, format,
mypy (101 source files) and dependency checks passed. Thirty hostile tests remain
explicitly opt-in skips; none is counted as containment acceptance.

## Remaining acceptance

## Durable operator queue, ALG-464

The coordinator added a distinct internal queue and operator-driven `run_next`
execution. Its namespace and job kind are separate from trusted jobs. Validated
source and pinned financial/data configuration are checked before persistence;
the non-executing admission helper cannot launch source. Each job uses its own
owned runtime, cancellation signal and bounded overall timer. Cleanup is confirmed
before result publication or terminal state; cleanup failure retains the active
nonterminal job, owner and scheduler lease. Startup reconciliation precedes
interrupted-job recovery, which never re-executes interrupted source.

Research execution now builds verified in-memory artifacts separately from registry
publication, while the ordinary trusted `run` preserves both steps. Independent
review reproduced a durable cancellation race between publication and completion.
`JobStore.publish_after_cleanup` now uses BEGIN IMMEDIATE to order independent
cancel writes against publication. A previously committed cancel never publishes;
a completion decision already holding the transaction commits before a later
cancel can write. Active tracking is cleared only after a confirmed terminal state.
The regression pair and existing trusted jobs passed 18 tests. Independent review
passed 68 focused tests and mypy, found no remaining behavioral blocker, and its
Ruff SQL-line finding was fixed; fresh Ruff/format and the 18 tests then passed.

The job database and immutable experiment registry are separate stores. A crash
after registry publication but before job transaction commit can retain a completed
artifact beside an interrupted job. This is not an atomic two-store commit and is
not evidence of a successful job; the preserved artifact must not imply eligibility.

Actual Windows/WSL `m22-real-queue-20261007T194406Z.json` passed two queued fixtures
with identical config/result/report archives and confirmed cleanup before publication.
After final formatting, `m22-real-queue-20261007T194838Z.json` freshly repeated both
and observed a real running infinite-loop fixture container before cancellation.
The job became cancelled only after confirmed cleanup; no cancelled experiment
was published and final container inventory was empty. These are bounded software
fixtures, not a strategy forward test or broker interaction.

Fresh integrated `checks-20261007T195318486076Z.json`: 791 passed, 35 skipped, Ruff,
format, mypy (102 files) and dependency checks passed. The separate explicit hostile
run `m22-hostile-current-20261007T195336Z.json/.xml/.txt` passed all 30 in 305.01s,
with no skips, empty before/after inventory and confirmed bounded keeper shutdown.
Its exact current sandbox source/image/profile identities are recorded. It remains
operator-backend acceptance, not an assertion that public queued API lifecycle
acceptance passed.

## Remaining acceptance after the queue slice

Public admission/API/CLI lifecycle acceptance, actual shutdown/restart/API-death/
transport-loss integration and final package replay remain required. The bounded
queue/replay/cancellation and operator hostile evidence above close only their own
recorded cases and hashes.
Installation freeze and independent final-package review remain required. Earlier
containment receipts apply only to their own hashes. Role participation is tracked
separately; HTTP success and synthetic performance are not release or alpha proof.
