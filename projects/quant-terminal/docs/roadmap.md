# Milestone roadmap

Owner authorised automatic continuation after passed gates on 2026-09-23. Stop on failed gates; no approval bypass. Each milestone follows PLAN → SPECIFY → TEST DESIGN → IMPLEMENT → UNIT → INTEGRATION → QUANT VALIDATION → DOCUMENT → REVIEW → GATE. Full original scope and exit gates are preserved in sources/owner-brief.txt.

- M0: PRODUCT SPECIFICATION AND ARCHITECTURAL BASELINE
- M1: PROJECT FOUNDATION AND DOMAIN MODEL
- M2: HISTORICAL DATA ENGINE
- M3: PORTFOLIO AND ACCOUNTING ENGINE
- M4: ORDER MANAGEMENT AND EXECUTION SIMULATOR
- M5: EVENT-DRIVEN BACKTEST ENGINE
- M6: PERFORMANCE AND RISK ANALYTICS
- M7: PROFESSIONAL BACKTEST REPORT
- M8: EXPERIMENT MANAGEMENT AND REPRODUCIBILITY
- M9: PARAMETER OPTIMISATION ENGINE
- M10: WALK-FORWARD OPTIMISATION
- M11: MONTE CARLO AND ROBUSTNESS ENGINE
- M12: ROBUSTNESS AND OVERFITTING TOOLKIT
- M13: TERMINAL API AND CLI
- M14: PROFESSIONAL TERMINAL UI
- M15: PERFORMANCE AND SCALABILITY
- M16: SECURITY AND STRATEGY SANDBOX
- M17: PAPER TRADING
- M18: LIVE BROKER ARCHITECTURE
- M19: ADVANCED PORTFOLIO AND RISK RESEARCH
- M20: ADVANCED ASSET CLASSES
- M21: PRODUCTION RELEASE HARDENING
- M22: FINAL RELEASE ACCEPTANCE (owner-approved extension, 2026-10-03)

Releases: M0–8 trusted backtester; M9–14 research terminal; M15–18 research-to-paper architecture; M19–21 advanced extensions and hardening. Advanced M19/M20 components require separate mathematical and asset-class fixtures; unsupported modes must reject explicitly. No live orders or deployment authorised.

M0 deliverables: eight specification documents, seven diagrams, ADRs, public contract ownership and failure semantics, golden-test design, all-role assignment map and independent review. No financial implementation at this gate.

Each later gate requires complete applicable unit/integration/property tests, lint, types, independent economic validation, documentation and an evidence report. M1 CI must run automatically and pass; local CI-equivalent checks are not evidence of a hosted workflow run. A local automatic commit hook may provide local automation, explicitly identified as local. No publishing without owner instruction.

Agent scheduling: all370 have a role scope and post-build interaction acceptance. Activate only tasks whose prerequisite gates passed; queue others. Bound concurrency to three disjoint writers. Agents must record real task/run/artifact evidence; no synthetic completion. No unsupported role may expand MVP scope.

M22 closes the existing M16/M21 and recorded all-role participation/terminal-interaction gates. It adds no broker trading, publication or deployment authorization. Final 1.0 requires fresh integrated evidence and independent review; failed gates remain recorded.
