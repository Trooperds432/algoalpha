# Chief Architect independent M0 review

Verdict: BLOCKERS to a fresh, unconditional gate PASS. The M0 specification coverage passes; the current gate check fails. This review is complete and does not authorise or implement M1.

Review date: 2026-09-23. Issue: ALG-373 (`d37c6c31-52fd-4ae8-9578-308aa2d2ec4b`). Agent: Chief Architect, role 12.1 (`fb4fdfd1-4ec3-46b2-bfbd-1965d0e14595`). Actual review run: `7c34265f-86c0-4992-b4a9-61f75a464a24`. Harness supplied the checkout. Earlier run `d29785bb-c5e3-4b25-ba25-24e175cc6dc2` failed workspace validation and is not review evidence.

## Gate blocker

**B1 — Medium severity, high confidence: the M0 gate cannot be rerun successfully on the current workspace.** `coordination/check_m0.py:21` asserts that the entire `src` directory is absent. Fresh execution of `python -X utf8 coordination/check_m0.py` exited 1 with `AssertionError: M0 must precede financial implementation`. The earlier assertions completed, covering eight document presence/size checks, seven Mermaid fences, 370 unique assignment identities, identity agreement with the issue map, distinct artifact paths and nonempty role tasks. They do not validate diagram semantics or financial correctness.

`coordination/milestone-state.json:4` already sets activeMilestone to 1, and its M0 entry records passed. The observed `src/quant_terminal` inventory contains foundation modules (clock, events, identifiers, serialization, configuration and logging). Directory existence is therefore insufficient evidence of premature financial implementation. This is a gate-check applicability defect in the advanced workspace, not proof of an economic implementation defect or an unauthorised transition. `docs/validation/m0-completion.md:14` records a historical successful check; this run cannot reproduce it. `git rev-parse --verify HEAD` also fails, so no committed M0 revision is available locally for isolated replay.

Owner/action: the external milestone coordinator should preserve this failure, establish an immutable M0 evidence snapshot, and provide a milestone-aware structural regression check that distinguishes permitted scaffolding/foundation from financial implementation. Preserve the original pre-implementation constraint; do not merely delete its assertion. Rerun the applicable check and reconcile the completion evidence before claiming a fresh gate PASS. See owner brief `docs/sources/owner-brief.txt:1884` (no milestone completion with failing tests) and `docs/testing_strategy.md:53` (fresh gate evidence). This reviewer has not changed the checker, source code, or milestone state.

## Specification and architecture assessment

| Requirement | Exact evidence | Result at M0 |
|---|---|---|
| Eight required documents, supported assets and bars | Owner brief M0 deliverables; `docs/product_requirements.md:22`; all eight files listed in checker lines 6-7 | PASS: equities/ETFs, daily/one-minute scope, explicit exclusions |
| Strategy and order lifecycle, ownership and failures | `docs/domain_model.md:8`, `:32`; `docs/system_architecture.md:12` | PASS: intents only, single ledger writer, immutable fills, residual rejection preserves executions |
| Causal event ordering | `docs/backtesting_conventions.md:3` | PASS at specification level: UTC, availability, economic versus observed time, next-full-bar eligibility, stable ties and no retroactive callback fills |
| Execution and explicit costs | `docs/backtesting_conventions.md:22` | PASS: gap/touch assumptions, deferred stop-limit, shared capacity, fill-time affordability, cumulative minimum fees, labeled zero-cost assumption |
| Accounting and corporate actions | `docs/backtesting_conventions.md:47`, `:64` | PASS: precision 34 Decimal, adverse tick rounding, FIFO costs, dividend receivables, split preservation, stale marks, insolvency limits |
| Metrics, benchmark and randomness | `docs/backtesting_conventions.md:74`, `:100` | PASS for baseline conventions: session returns, A=252, rf=0 default, sample Sharpe, drawdown, null reasons, absent benchmark, recorded seed and OOS separation; later metrics need additional specifications |
| Data versioning and reproducibility | `docs/data_model.md:3`, `:20`, `:24`, `:33` | PASS as design: hashes, immutable snapshots, source/license/PIT fields, atomic completion, failed run preservation |
| Test design and independent arithmetic | `docs/testing_strategy.md:8`, `:21`, `:28`, `:39` | PASS as design: 33 planned cases, exact ledger/toleranced analytics policy, differential validation; published arithmetic checked below |
| Trust and integration boundaries | `docs/security_model.md:12`, `:18`, `:24`, `:37` | PASS as design: trusted strategies before M16, no false sandbox claim, authenticated future service boundary, no broker secrets in strategies |
| Architecture decisions and staged delivery | `docs/adr/0001-reference-engine.md:3`, `:14`; `docs/roadmap.md:3` | PASS coverage: context/decision/alternatives/advantages/disadvantages/consequences for both ADRs; recorded decision status needs reconciliation |
| Role coverage and actual participation | `docs/domain_model.md:53`; checker lines 13-19; `docs/testing_strategy.md:58` | PASS assignment structure only. Actual participation by 370 roles is not established and is not inferred from roster size |

Engineering-quality verdict: coherent modular design for M0, with B1 preventing fresh gate verification. Financial implementation, security isolation, performance and hosted CI are outside this review and receive no PASS.

## Seven diagrams

All seven source diagrams were read independently against the owner brief's M0 diagram list. No rendering tool was run; this is a source/flow review, not visual rendering evidence.

| Diagram in `docs/system_architecture.md` | Review |
|---|---|
| Strategy/execution/portfolio, lines 23-34 | PASS: data to strategy, intent, risk, broker, fills, ledger and read-only feedback; order lifecycle detail is supplied by domain contracts |
| Historical data, lines 36-44 | PASS: validation precedes hashed immutable storage and availability-filtered feed |
| Experiments, lines 46-55 | PASS: canonical manifest, bounded queue, worker, immutable artifacts, derived index |
| Optimisation, lines 57-65 | PASS: stable trials, IS-only data, all results, deterministic filtering and frozen proposal |
| Walk-forward, lines 67-75 | PASS: frozen selection precedes OOS execution; nonoverlapping stitching |
| Monte Carlo, lines 77-85 | PASS: explicit sample/dependence assumptions, named seeded generators, paths, metrics and limitations |
| Reporting, lines 87-95 | PASS: immutable results feed metrics and exports, with hashes and manifest references |

The worker box is not evidence of hostile-code isolation; the text at architecture lines 103-104 and security lines 12-16 correctly limits that claim.

## Nonblocking specification follow-ups

- **Medium, high confidence, before M6:** `docs/backtesting_conventions.md:82` supplies the downside-deviation expression but not the full Sortino numerator and annualisation formula. Its MAR convention must specify units/conversion. Line 98 explicitly defers remaining metric fixtures. Complete the definition before implementing or comparing Sortino; this does not prevent documenting the M0 baseline.
- **Low, high confidence:** ADR statuses remain proposed/pending at `docs/adr/0001-reference-engine.md:3` and `:16`, while `docs/validation/m0-completion.md:4` says PASS. The coordinator should record the accepted revision and decision date, rather than infer acceptance from this review.
- **Medium, medium confidence, before M4/M5:** clarify DAY expiry when bar publication is delayed past session close (`docs/backtesting_conventions.md:12`, `:15`, `:37`). Specify how an economically eligible final-session fill, observed later, interacts with expiry and callbacks. The existing ban on retroactive fills is sound but needs a concrete delayed-close fixture to prevent competing implementations.

## Fresh verification and limits

Environment: Windows PowerShell, Python 3.13.15; project cwd `C:/Users/Jonat/algoalpha/projects/quant-terminal`. Git metadata exists, but HEAD has no revision; reviewed inputs are identified by hashes below. Existing untracked source, tests and documents were preserved.

1. Read project AGENTS.md, roadmap and milestone state before mutation; read all eight M0 documents, both ADRs in the one ADR file, the owner brief at `docs/sources/owner-brief.txt`, prior completion report and checker. The issue's shortened `sources/owner-brief.txt` resolves to the actual file under `docs`.
2. `python -X utf8 coordination/check_m0.py`: exit 1, B1 above. Failure retained; no retry or altered test oracle.
3. `git rev-parse --verify HEAD`: exit 128, `fatal: Needed a single revision`. This limits baseline provenance; Git was not repaired or committed by this review.
4. Independent inline Python Decimal/Fraction assertions via `python -X utf8 -`: exit 0. Recalculated published examples without importing project code: 1000-4*100-1=599; first NAV=1019; partial sale cash=818; final NAV=1034; closed net P&L=18.5. Split value remains 600; dividend receivable plus ex-date holdings remains 600; [100,120,90,108,120] has 25% peak drawdown and returns [1/5,-1/4,1/5,1/9]. This validates the examples, not an engine.
5. No implementation tests, renderer, dependency installs, broker/TradingView calls, external research, delegation, or production changes. Only this assigned report was created in the repository.

Sources are the owner-supplied local brief and local specifications, inspected on 2026-09-23. Original publication dates are not supplied. Numerical samples are synthetic, with no historical market sample dates or market-data entitlement claim. Transaction costs are explicit in the checked fixture (two fees of 1); broader cost models remain proposed contracts. There is no strategy backtest, risk approval, paper simulation, elapsed simulation period, or professional approval in this evidence.

Disposition: complete ALG-373 as an independent review deliverable with blockers reported to its coordinator. Completion of this review is distinct from passing M0. No child issues or later-milestone work were started.

## Reviewed input SHA-256 hashes

- AGENTS.md: d398555f69969844dcea96f91e90e1a3f80ba6a5221a72f39e4a298b81da980c
- docs/product_requirements.md: 811eb617b22963ac921e92d738209d7064c2f78a61a37938f78d78ec90930888
- docs/system_architecture.md: d53e34dce6c238315878911a788d49390b149f9ddec98bd0b419015d941f5923
- docs/domain_model.md: 97a1813698568eddb1a465be13a02486f24bc6691e9473132622c3b758f5abee
- docs/backtesting_conventions.md: f6f08f786f4bbd706e31f4610daa2f01f9a624caebab25d333a5b364887d46ce
- docs/testing_strategy.md: 817e5d0971d8b272fdb2cfb142cd08852397f3a64ccfbc5decee603e153ef60d
- docs/security_model.md: a57a15c7829b4e9c5cb01ecd75a460f46e852d7e56826d74dd63b080fef3f663
- docs/data_model.md: 2b72b9fdfb403c1559de4b3dbbf2bbefa260385965e26492bea66296f49db869
- docs/roadmap.md: acea3eda9c950eabc5546dabf84aef3121041c615d9166b9b01503d7d49e840b
- docs/adr/0001-reference-engine.md: 30289fdfe5fa0b2227b9bbf4f2abbad4666997c90b17585be5854568eb2d4408
- docs/sources/owner-brief.txt: c18dae786b2145bbd606494a6aaf52dbd3bece61616dee3f81ca0975a6eef02e
- docs/validation/m0-completion.md: 13cf00e1918823cb4a08a4cbe2e487bcd9f3ffc5879ca644cc72346badd972c7
- coordination/check_m0.py: 5afba60458e351387557a95e4858cbce6adb281a57018a749ef412df47f6a04f
- coordination/milestone-state.json: d60998b5c6582b61e32f5a6ab3b4d0ddbde842eab8e1e50b5452922c9d4b74cc
- coordination/agent-assignments.json: 96a3156f896748b8e967d7ea497a0c5e955bdbf3aabd50802cf6da96435698d1
- coordination/paperclip-issues.json: 45a3e09f262229a21ee9e4dcc762ef1f9928194ff1d540db2f39394b793af036
