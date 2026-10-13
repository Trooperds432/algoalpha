# Quantitative Research & Trading Terminal

Python-native, local-first quantitative research terminal for AlgoAlpha.

Current state: Milestones 0-15 and M17-20 have passed. The local GUI at `/ui/`
includes a Paper Trading workspace with virtual funds and simulated execution.
The owner explicitly prioritised M17 while M16 remained under verification.
M16 is not passed: final independent lifecycle cleanup/audit evidence remains open;
public arbitrary Python remains disabled. M18 provides demo-only Trading 212 broker
architecture and reconciliation contracts; external connectivity and order
submission remain disabled, and no strategy forward test has been started.
M19 allocation and risk research is implemented in the Portfolio Research workspace,
API and CLI; its gate evidence is recorded in docs/validation/m19-portfolio-research.md.
M20 asset-specific accounting and research is implemented in the Advanced Assets
workspace, API and CLI. M21 provides a local 1.0.0rc1 release candidate; M22 Final Release Acceptance is in progress,
backup/restore, schema upgrades and runtime observability. Final release remains
withheld pending M16 and recorded participation/interaction by all 370 roles.
See [release operations](docs/release-operations.md).

Start with [product requirements](docs/product_requirements.md),
[architecture](docs/system_architecture.md), [financial conventions](docs/backtesting_conventions.md)
and [roadmap](docs/roadmap.md). The full owner brief is preserved in
docs/sources/owner-brief.txt; extracted PDF context is in docs/sources/research-pdf.txt.

All 370 employees are mapped in coordination/agent-assignments.json. Their work is
staged by domain and milestone, with recorded evidence required for completion.
Later, agents will use the same terminal API/CLI as the human UI and record real
task outcomes. That interaction is an acceptance requirement; issue plans and
dispatches are not evidence of completed employee interaction.

For source-based M13 examples, see [headless usage](docs/headless-usage.md).
For the local interface, see [UI usage](docs/ui-usage.md).
For paper-session controls and recovery, see [paper trading usage](docs/paper-trading-usage.md).
For allocation models and risk diagnostics, see [portfolio research usage](docs/portfolio-research-usage.md).
For FX, futures and options models, see [advanced assets usage](docs/advanced-assets-usage.md).
