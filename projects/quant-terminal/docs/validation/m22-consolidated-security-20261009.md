# Current security consolidation — ALG490

Current candidate: `C:/Users/Jonat/algoalpha/runtime/quant-terminal-m22-20261009-audit-identity.zip`, SHA256 `5c8c4f454c23c5df5d27b21e42fcf82921daccf6b36345bdb79292e97b3db97c`. This is an acceptance candidate, not an approved final release. The September 30 M16 gate record is historical.

The immutable verification copy separates source-dependent acceptance from concurrent employee artifact writes. Root and independent reviewer `/root/consolidated_release` checked all 127 shipping manifest entries against the archive, frozen copy and current tree. All 164 source/test hashes in `checks-20261009T123902671451Z.json` match the current tree. Ruff, formatting, mypy and dependency consistency passed; regression is 858 passed, 35 documented skips. Actual hostile containment is a separate opt-in check and must pass without skips.

Current actual runtime reports each carry 104 matching source hashes:

| Boundary | Passing report |
|---|---|
| HTTP execution, running cancellation, CLI deterministic replay | `m22-real-gateway-20261009T123452Z.json` |
| Transport keeper loss, readiness revocation and ownership cleanup | `m22-gateway-transport-loss-20261009T123730Z.json` |
| Timeout and cleanup | `m22-gateway-timeout-20261009T193746Z.json` |
| Normal ASGI graceful shutdown with an observed running container | `m22-graceful-shutdown-20261009T194123Z.json` |
| Abrupt API parent death and recovery | `m22-gateway-parent-death-20261009T194400Z.json` |
| Required audit-write failure blocks admission and retains ownership until cleanup | `m22-gateway-audit-failure-20261009T195028Z.json` |
| Publication crash hides the orphan, rejects six read paths and preserves immutable bytes | `m22-publication-crash-20261009T195116Z.json` |

ALG488 preserves only an immutable last-successfully-verified forensic backend identity after readiness revocation. It never reuses that snapshot to authorize admission or execution. Failed initial probes and failed cleanup cannot create verified identity. Root-cause red regressions were preserved; independent review passed 34 tests with three documented platform skips, and the full regression above includes the change. Actual keeper-loss events retain Podman 5.8.8 and complete image provenance with `identity_status: last_verified` while capabilities are unavailable. Timeout events retain `currently_verified` when readiness remains valid.

Default public admission remains disabled. An explicit operator configuration must pin the image, seccomp profile, source/dependency identity and verified containment evidence; unsupported or changed environments reject. The fixed image is `sha256:083a4ee8f04d4e4c91bc735931eccd5780c8e4e8ba7acfae04b6234fc573fc29`. These are synthetic acceptance fixtures, not broker access, strategy eligibility, investment suitability or universal host security claims. Normal trusted built-in subprocesses remain distinct from this adversarial container boundary. The graceful test requests normal server shutdown through a controlled supervisor seam; it does not prove delivery of every Windows OS signal.

The current hostile suite `m22-hostile-current-20261009T195208Z.json` and its XML/text companions passed all 30 tests in 318.38 seconds, with zero failures, errors or skips. All 11 component/test hashes match current files; inventories are empty and keeper cleanup is confirmed. Both root and `/root/consolidated_release` independently reconciled those bytes and the complete requirement matrix above, including the seven runtime reports and 127 shipping/164 regression hashes.

**Technical M16 gate: PASSED on 2026-10-09 for the specified tested Windows/WSL/rootless Podman configuration.** No material technical finding remains within that scope. Default admission remains disabled; this does not install a production acceptance receipt or approve another host. M21/M22 separately require complete employee participation and final release evidence. Current clean-candidate storage acceptance is recorded in `m22-current-storage-20261009-20261009T195508Z.json`. The clean replay's initial environment-mismatch rejection is preserved in `m22-clean-gateway-20261009T195819Z.json`; root removed inherited PYTHONPATH before the corrected replay, without changing product code or weakening admission checks.
