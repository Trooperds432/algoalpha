# M22 gateway integration, 2026-10-08

ALG-465 (37450751-ef40-45d4-a0e0-77a54f154fc1) implements the privileged local
gateway and separate public API/CLI queue. ALG-443 runtime ownership and ALG-464
queue lifecycle prerequisites received serialized coordinator fixes. M16/M21/M22
remain in progress; this report does not approve final release.

## Implementation and independent review

Default admission still rejects before source parsing/staging. Operator metadata
binds current source, host, dependency lock and backend; HTTP cannot supply it.
Proofs are confined sibling files beside the operator receipt. Fixtures now use
local synthetic proof files explicitly marked as not containment evidence.
Public budget/dependency/source constraints and shared immutable provenance remain
enforced. Source text is omitted from public job views.

Independent reviewer `/root/m22_gateway_review` reproduced two P2 defects without
Podman: the newest 100 history rows hid an older queued job, and startup contention
left the local scheduler lease stuck. Root added full-queue EXISTS readiness and
proved never-started-owner cleanup under the local backend lock. Started or failed
cleanup retains the existing ownership/reconciliation/audit/keeper requirements.
CLI operational failures return JSON and API readiness loss rejects before admission.
The reviewer freshly passed 69 affected tests in 43.40 seconds, Ruff and mypy;
both confirmed blockers were fixed. This reviewer is a Codex subagent, not a new
Paperclip employee participation record.

## Preserved failed evidence

The first history regression incorrectly released its test pause after 10 seconds
while preparing 100 requests; it failed with fixture owner unavailable. Root's
focused run recorded 1 failed/26 passed; independent runs also preserved failures.
The corrected regression uses explicit release and bounded direct history setup.
The startup contention regression failed before the fix with two host acquisition
attempts instead of one, establishing the intended mechanism.

`m22-real-gateway-20261008T120624Z.json` failed because the harness incorrectly used
RuntimeOwner as a context manager. The harness now explicitly starts/closes it.
`m22-gateway-parent-death-20261008T121416Z.json` failed during initial inventory
because the WSL engine was cold; the corrected harness first warms the fixed
distribution with a bounded read-only Python version query.
`checks-20261008T121458171261Z.json` passed 811 tests, with 35 documented skips,
Ruff, mypy and dependency checks, but failed CLI formatting. That failure is retained;
formatting was corrected before the final verification rerun.

## Actual operator acceptance-testing evidence

`m22-real-gateway-20261008T120747Z.json` passed real Podman through the HTTP adapter
and a separate CLI process, with identical result fingerprints and empty inventory.
`m22-real-gateway-20261008T121132Z.json` added actual running-container HTTP cancel:
job fc866a51cc084b53981cf0542d776f09 became cancelled after cleanup, with no experiment
published. The successful HTTP/CLI runs remain visible in their disposable state.

`m22-gateway-parent-death-20261008T121435Z.json` passed actual loopback API child
termination while its source container ran. Inventory was empty before restart;
job 321fdb799d704deaad53f11a18e187b9 recovered interrupted with no result and only
one launch of its source hash. No automatic source rerun occurred. Only the
disposable API child was killed; the user's normal terminal was not terminated.

Every real report includes exact source hashes. Backend/proof provenance is
referenced through the disposable state's operator receipt and audit rather
than embedded in every report.
These bounded software fixtures use acceptance-testing operator receipts; historical
proof inputs are not represented as final current-build approval. The final formatting
change requires a fresh compatible receipt and rerun, recorded below when complete.

## Outstanding gates

Current integrated hostile tests, transport loss/audit failure/timeout acceptance,
final package freeze/replay and independent security/release review remain required.
Cross-database crash artifacts must remain distinct from successful job evidence.
At the start of this slice the independently verified ledger was 47/370; no employee vote,
professional approval or alpha performance has been fabricated.

## Final-byte rerun

`checks-20261008T121933230547Z.json` passed all five checks: 811 tests passed,
35 documented skips, one existing Starlette warning; Ruff passed, 162 files were
formatted, mypy passed 103 source files, and dependency requirements were consistent.
The 30 hostile tests skipped in the ordinary suite are checked separately with
explicit real-container opt-in; their skip is not acceptance evidence.

`m22-real-gateway-20261008T121544Z.json` passed the compatible final-byte HTTP/CLI
and actual running HTTP cancellation fixtures, matching fingerprints and leaving
empty inventory. `m22-gateway-parent-death-20261008T121737Z.json` passed the compatible
final-byte abrupt API death/restart fixture: job 348a4a35c0be4cbfa43df88be5034bdd
recovered interrupted, no automatic rerun, and empty pre-restart/final inventory.

`m22-hostile-current-20261008T121848Z.json/.xml/.txt` passed all 30 hostile tests
in 291.84 seconds with no skips, empty before/after inventory and confirmed keeper
closure. Its current sandbox hashes match the final-byte gateway evidence.

`m22-gateway-audit-failure-20261008T122354Z.json` passed actual public queue audit
failure and recovery. Job 87245c4154d84302a70e33a6ba0db3e0 remained running with no
result while cleanup recording failed; admission blocked, a competing owner was
rejected, and the scheduler retained its lease. After restoring only the disposable
audit path, cleanup completed, the job became interrupted, the lease released,
and inventory was empty. No experiment was published.

`m22-gateway-transport-loss-20261008T122551Z.json` passed loss of the disposable
runtime keeper during an observed running source container. Only that keeper's
fixed control channel was closed early; no global WSL shutdown occurred. Subsequent
admission rejected, the source job failed with no result or experiment, and container
inventory was empty before gateway close.

`m22-gateway-timeout-20261008T122714Z.json` preserved an unsuccessful harness assertion
expecting wall_timeout. Conmon killed the sleeping source before the outer client
timer, producing container_exit_failure. The corrected fixture directly inspected
a running container's configured five-second Timeout and recorded elapsed launch-to-
termination time; `m22-gateway-timeout-20261008T122903Z.json` passed. The controlled
source requests a 60-second sleep, never receives cancellation or keeper loss, and
fails without publishing an experiment. Exact audit reasons remain recorded.

These fresh actual fixtures close their specific transport/audit/timeout evidence
gaps; independent final package/security review, installation replay, cross-database
crash qualification and complete role participation remain outstanding. Three actual
data-role findings accepted on 2026-10-08 bring independently reviewed participation
to 50/370. Final release remains unapproved and default submission remains disabled.

## Independent bounded evidence acceptance

On 2026-10-08 `/root/m22_gateway_review` independently accepted ALG-465's bounded
API/CLI integration evidence. All six passing reports' hashes match current files;
the broad check hashes match 161 source/test files, and hostile JUnit shows 30
passed with no skips. Durable jobs and the registry corroborate HTTP/CLI fingerprints,
cancellation, interruption, audit recovery, keeper loss and timeout failures without
publication. Actual audit entries show one launch per fault source and preserve
scheduler_death, keeper_transport_lost and container_exit_failure.

Observed sleeping-source termination was 7.51 seconds after launch, with an inspected
five-second container Timeout; this establishes neither an exact cleanup deadline
nor every transport/audit failure mode. The transport fixture covers this keeper's
loss and the audit fault occurs before execution progresses. Successful HTTP fixtures
use TestClient; abrupt death uses an actual loopback API subprocess. Cross-database
publication qualification, frozen installation replay/final security review and 320
remaining role reviews still prevent final release approval.

## Candidate installation and campaign recovery

Local archive `C:/Users/Jonat/algoalpha/runtime/quant-terminal-m22-20261008-gateway.zip`
is an explicitly nonfinal 1.0.0rc1 candidate (126 ZIP entries), SHA256
36c82dc76663b2dadf3133444232041131f64ae89f90233ad6189c7c39c8d63d.
Build/manifest verification passed. The new, separate clean installation used
`uv sync --frozen --offline` with Python 3.13.15 and installed the frozen runtime.
Version, disabled default isolated capability, risk-parity output 0.6/0.4 and
futures settlement final cash 29000 passed from that installation.

`m22-clean-gateway-20261008T123651Z.json` passed actual clean-install loopback
health/readiness/capability/UI responses, exact installed source-hash comparison
and a real isolated CLI replay using the clean environment. Result fingerprint
6de31ff1a3ff6b01902850de7e956d9d756b7fcfae40089e970ccb9dd89a3455 matches development
HTTP/CLI evidence. The temporary port-8766 smoke process was stopped after checks;
the existing port-8765 terminal remains separate. This candidate smoke does not
approve final installation, UI interaction or final security/release acceptance.

ALG-471 (2c122a76-cdf9-4f66-a144-15336f0b8f22) addresses the actual coordinator bug
observed for 7.9: scheduled_retry was prematurely treated as a terminal failure.
The same actual run subsequently succeeded. That earlier observation is preserved,
and the completed observation references the same run rather than inventing a retry.
The new public-campaign regression failed before the fix by attempting a new employee
dispatch while the only active slot was occupied. After classifying scheduled_retry
as active, all 14 dispatcher tests and focused Ruff/format checks passed. This changes
coordination behavior only; current sandbox/package bytes remain unchanged. Independent
dispatcher review and a fresh integrated regular suite are being completed.

Two more actual successful findings bring independently reviewed participation to
52/370. Cross-database crash qualification, final package/security review and the
remaining 318 roles still prevent M16/M21/M22 final release approval.

## Continued acceptance: packaged operator documentation

ALG-479 (`6adfbdb4-3d83-4e00-82be-7d32ab5141bb`) adds the existing isolation operator guide to the candidate document list and links it from README. Independent reviewer `/root/m22_gateway_review` confirmed the earlier omission was a final-candidate documentation gap. Both root and reviewer ran the eight distribution tests successfully.

An explicitly interim documentation-check archive, `C:/Users/Jonat/algoalpha/runtime/quant-terminal-m22-20261008-operator-docs.zip`, has SHA256 `bd80bb52f8fc6d39bf3a89710063305651751987cc5f0a5be470546b97951568`. Root and independent reviewer verified exact guide bytes, manifest checksum and README link. It is not a frozen integrated candidate: ALG-476 security audit changes remain in progress and require fresh verification and rebuilt installation evidence. No milestone or final release is approved.
