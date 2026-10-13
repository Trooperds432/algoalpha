# M12 completion report

Status: **PASS**, 2026-09-27. Supersedes the partial m12-progress.md report.

Implemented immutable scenario identities, real M5/M8 scenario reruns for parameter,
commission, slippage, observation delay, start/end, subperiod, declared regime and
universe changes; durable outcome callbacks retain invalid and failed attempts.
Source/data/configuration mismatch and recorder failures stop the campaign. Delayed
observations hide newer history while executions use later original market bars.
Single-instrument delay requires strictly ordered availability and records the
undelivered tail. Scenarios with no observations or executable remaining bar fail.

Neighborhoods preserve explicitly declared missing grid levels and distinguish
broad plateaus, isolated optima, non-optimal centers and inconclusive samples.
Conditional percentile bootstrap intervals retain every replicate and withhold
intervals if Sharpe replicates are undefined. PSR preserves its explicit IID and
sample attestation prerequisites. Complete-population M9 method-support reports
include failed/infeasible attempts. DSR, White Reality Check and purged CV remain
explicitly unavailable with prerequisite designs, consistent with the owner's
investigation/design scope; no invented statistics or strategy approvals.

Fresh full gate: [checks-20260927T175050551113Z.json](checks-20260927T175050551113Z.json).
Ruff lint/format pass; mypy 58 source files pass; **228 tests passed**, two existing
Windows symlink capability skips; dependency consistency clean. Five new real
integration checks cover repeat fingerprints/reports, all perturbation categories,
causal fill prices/times, source drift, factory failure, cancellation, recorder
failure and fatal identity mismatch. Full suite runtime 11.20 seconds; not a
performance benchmark. No golden financial outputs changed.

Independent Codex review: /root/m12_bootstrap reviewed models and root engine/adapter,
reported original plateau/finite-value defects, then verified fixes with 28 focused
tests and no remaining blockers. /root/m7_implementation_review independently
reviewed bootstrap and its six tests with no blocker. These are Codex collaborators,
not evidence that Paperclip employees ran. Paperclip participation is tracked
separately through real role issue/run IDs.

Critical behaviors: exact finite Decimal comparisons, immutable nested changes,
source/result fingerprint retention, unavailable-value propagation, named sample
assumptions and failed-candidate preservation verified. Trusted local strategies
only; no adversarial isolation before M16. No broker calls or order submissions.
Complete-grid consumers must supply the original M9 declared levels; observed-only
fallback cannot infer omitted cells. PSR's caller adapter must attest actual
calendar alignment; no automatic attestation from arbitrary timestamp arrays.

Documentation: docs/m12-robustness-contract.md. Technical debt: delayed multi-asset
mapping, numerically validated dependence corrections and ML label-interval
contracts remain explicit future extensions. No ADR change to the core engine.

Acceptance: PASS perturbation reproducibility; PASS narrow/broad distinction;
PASS missing/failed evidence preservation; PASS uncertainty assumptions visible;
PASS full local checks and independent review. **READY FOR NEXT MILESTONE**.
