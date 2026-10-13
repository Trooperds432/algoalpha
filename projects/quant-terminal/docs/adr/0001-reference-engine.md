# ADR 0001 — reference engine and staged delivery

Status: accepted at M0 gate.
Context: owner requires rigorous Python strategy research and eventual mode parity.
Decision: original modular Python event reference engine, Decimal ledger, restricted
visible data contracts, immutable results, SQLite+Parquet initial storage, staged gates.
Alternatives: MATLAB foundation rejected for dual-runtime/licensing overhead; vectorised
only rejected for execution semantics; immediate LEAN backend deferred behind EngineAdapter;
microservices deferred until profiling warrants complexity.
Advantages: explicit semantics, testable accounting, simple local deployment.
Disadvantages: implementation/validation work and slower reference throughput.
Consequences: no acceleration before golden parity; no polished UI before M0–8.

# ADR 0002 — role participation and safe sequencing

Status: accepted at M0 gate.
Context: user requests participation by all370 existing employees with distinct roles.
Decision: all 370 agents receive role-specific staged assignments and later interaction
acceptance. Use at most three independent writers, one integration owner, one ledger
writer and independent validation. Specialist asset roles design later extensions and
review unsupported-mode rejection now; they do not expand the MVP.
Alternative: 370 simultaneous workers rejected for shared-file collisions, duplicate
work, cost and loss of review independence. Consequence: participation spans milestones,
not every agent simultaneously coding. Agent execution must be evidenced, not simulated.
Advantages: traceable role participation, independent review and limited write contention.
Disadvantages: coordinating370 assignments takes time; many roles contribute review
or acceptance rather than code, and all must eventually produce real interaction evidence.
