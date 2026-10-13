# M9 parameter optimisation contract

## Objective

Provide reproducible grid and random search over a declared strategy parameter space. Optimisation is an experiment producer: every candidate, including invalid, failed, infeasible, and successful trials, is retained in M8's immutable ExperimentStore. It must never make a strategy look better by silently dropping outcomes.

## Search specification

An immutable `OptimizationSpec` records a stable optimization ID, base strategy/source identity, exact dataset snapshot identities, interval, capital, currency, all execution/backtest and analytics settings, parameter dimensions, search method, candidate budget, random seed, objective, constraints, and engine version. Inputs are canonicalized and fingerprinted. Grid candidates have deterministic lexicographic order. Random candidates use a versioned SHA-256 counter and rejection sampler, seeded by the specification, with sampling without replacement; the same specification produces the same ordered candidates independent of Python RNG implementation. The sampler version is part of the fingerprint.

Initial methods are finite Cartesian grid and seeded random sampling over declared finite/discrete or bounded numeric dimensions. The first implementation will favor typed finite choices to avoid ambiguous float stepping. Decimal values are canonical strings, limits and dimensions are validated, and preflight estimates grid cardinality before any run is started. A bounded trial budget is mandatory.

## Objective and constraints

Support named M6 metrics: Sharpe, Sortino, CAGR, Calmar, profit factor, and maximum drawdown, plus a trusted custom objective adapter that emits a typed `Metric` into the trial record. Direction is explicit (`maximize` or `minimize`). Constraints include minimum trades, maximum drawdown, maximum turnover, maximum leverage, and minimum exposure. Each constraint has typed units and strict comparison semantics documented in the implementation. Missing metrics make a trial ineligible with a reason; they are never coerced to zero. Failed, infeasible, or unavailable trials remain queryable.

## Execution and result handling

A caller-supplied trusted runner evaluates one candidate against the pinned dataset/configuration and returns typed M5 result, M6 analytics, M7 report artifacts, and the candidate identity. M9 does not import stored strategy source, launch arbitrary code, connect to a broker, or submit orders. Every outcome is registered as an immutable M8 experiment with the optimization ID, candidate index, parameter values, outcome status, and failure/infeasibility reason. Re-running an identical specification must reproduce candidate sequence and each trial's exact deterministic economic fingerprint; mismatches are retained and surfaced.

The optimizer reports the complete trial population and objective/constraint outcomes. It does not declare maximum in-sample Sharpe to be the best strategy, recommend capital allocation, or imply out-of-sample validity. Walk-forward testing belongs to M10.

## Analysis outputs

Provide deterministic trial records suitable for tables and later API/UI use, including candidate parameters, the complete M6 metric set, objective value/status, constraints, M8 run ID, input/result fingerprints, and execution duration as informational metadata. Provide 1D/2D parameter response-surface data, parameter/metric distributions, and adjacent-candidate metric changes with explicit missing-value reasons. Neighbor stability is a diagnostic over nearby candidates, not a selection guarantee. No UI is required in M9.

## Failure and safety semantics

Validate the whole specification and budget before execution. Reject duplicate IDs and malformed ranges without partial launch. Per-trial runner errors are captured as failed outcomes while later candidates continue only if the error is classified as trial-local; storage, data-integrity, or identity errors stop the campaign immediately. Every started trial leaves either a durable M8 run or a visible campaign failure record; never silently mark a partial campaign complete. Cancellation stops before the next candidate, preserving completed trials and a resumable cursor. Resume verifies the original specification fingerprint and existing contiguous candidate identities before continuing.

## Required verification

Test canonical spec fingerprints; grid order/cardinality; deterministic seeded random candidates; no replacement; budget enforcement; objective direction; strict constraint boundaries; missing metric handling; complete retention of pass/fail/infeasible trials; M8 registration and exact identities; campaign resume/cancel; changed-data/config refusal; trial-local error continuation versus integrity fail-stop; repeat equality/mismatch; and no broker/order side effects. Run tests with a small actual M5/M6/M7 and M8-backed optimization.

## Exit gate

A bounded search with the same immutable specification, data and deterministic trusted runner emits the same candidate sequence and exact per-trial results, retains every attempted candidate, explains every exclusion/failure, and presents diagnostic surfaces without overstating in-sample selection. A passed M9 gate advances to M10 walk-forward optimization; it does not qualify any strategy for production capital.
