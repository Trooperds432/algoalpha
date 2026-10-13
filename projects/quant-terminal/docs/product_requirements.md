# Product requirements — M0 baseline

## Authority and outcome
The owner brief controls requirements. The 35-page research PDF informs design;
where its compressed roadmap differs, use the explicit 22-milestone owner roadmap.
The goal is a reproducible Python strategy research workflow comparable in function
to MultiCharts/AmiBroker, with independent event execution, accounting and analytics.
Profitability or durable benchmark outperformance is not a software acceptance test.

## Releases and users
- 0.1, M0–8: trusted backtester, data, ledger, orders, analytics, HTML reports and provenance.
- 0.2, M9–14: optimisation, walk-forward, Monte Carlo, robustness, API/CLI and React UI.
- 0.5, M15–18: performance, isolation, realtime paper architecture and one broker boundary.
- 1.0, M19–21: modular advanced research/assets, hardening and release evidence.

Researchers write trusted Python strategies and compare every trial. Traders inspect
fill timing and execution costs. Risk/validation independently challenge results.
Data and engineering roles maintain data and services. Business, legal, audit and
workplace roles inspect role-relevant governance, usability and evidence; they do
not become trading agents merely because the terminal is available.

## Initial supported scope
Equities and ETFs, daily and one-minute completed OHLCV bars; multiple instruments
in one account currency. An explicit exchange calendar and instrument metadata are
mandatory. XNYS/XNAS are initial calendar targets, not a claim of calendar coverage
until validated. Synthetic fixtures are labeled and never presented as market evidence.
Reference accounting supports signed quantities and short fixtures, but deployable
short strategies remain blocked without explicit borrowing/financing assumptions.
Initial backtests use a cash long-only policy unless configured simulation assumptions
explicitly authorise shorts and leverage. No options, full futures, FX conversion,
L2/L3, HFT, production live deployment or multi-broker scope initially.

## Required user workflow
Register immutable dataset → author strategy → specify parameters and financial
assumptions → submit reproducible backtest → inspect orders/fills/positions/trades,
NAV, costs and metrics → export HTML/JSON/CSV/Parquet → rerun and compare → optimise
all trials → freeze selections using IS only → stitch OOS → bootstrap and perturb
assumptions → produce a report showing limitations. Later use unchanged strategy
business logic against a paper broker, with fault/restart/reconciliation tests.

## Acceptance requirements
PR-01 One Strategy lifecycle emits intents, never changes balances.
PR-02 Every fill has an order, timing/model/risk decision and traceable ledger effect.
PR-03 Future or unavailable data is rejected by the data capability boundary.
PR-04 Economic results reproduce with fixed inputs; run identity/time may differ.
PR-05 Golden scenarios independently reconcile NAV and costs; failures block gates.
PR-06 Metrics define formula, annualisation, edge cases and tolerance.
PR-07 All trials/windows/failed experiments are preserved, not only winners.
PR-08 CLI and UI call the same application service contracts.
PR-09 Strategies never receive broker secrets; live operation remains separately gated.
PR-10 All 370 roles have assigned scope, evidence, and later terminal interaction records.
PR-11 Final v1 acceptance executes the complete research lifecycle and a paper parity
demonstration; no release claim before it passes.

## Decisions and limits
No purchased datasets or subscriptions. Benchmark is explicitly none unless a
versioned compatible series is supplied. No automatic liquidation at run end.
The existing ETF desk is a reference artifact only, not an accepted implementation.
No UI polish before the M0–8 financial core. Agent count is not computational parallelism.
Broker environment setup is not a strategy test or permission to transact.
