# M6 performance and risk analytics gate

Status: **PASSED** on 2026-09-27.

The analytics package consumes immutable `BacktestResult` snapshots and
explicit assumptions. It emits typed metrics with units, sample counts,
availability status, and edge-case reasons. The implemented scope includes
returns and annualized risk, drawdowns and recovery, closed FIFO trade
statistics, exposure, leverage, turnover, execution costs, distribution and
tail measures, seeded assumption-based risk of ruin, and explicit benchmark
unavailability while M5 only supplies price returns.

Definitions, sample rules, edge cases, assumptions, and limitations are in
[`m6-analytics-contract.md`](../m6-analytics-contract.md). Independent golden
fixtures cover hand-calculated session returns, Sharpe and volatility, recovered
and unrecovered drawdowns, FIFO partial closes and reversals with proportional
fees, long and short excursion direction, entry-baseline excursions, insolvency,
VaR/ES tails, seeded risk of ruin, custom confidence labels, no-trade cases,
breakeven run handling, and Decimal-context determinism.

The original M6 gate passed in
[`checks-20260927T112815213791Z.json`](checks-20260927T112815213791Z.json):
Ruff lint, formatting, strict mypy, all **134 tests**, and `pip check`. Later,
M7 added public read-only helpers for sampled NAV/returns, FIFO closed trades,
and trade excursions so the report uses M6 conventions directly. Those source
and contract changes were included in the complete **139-test** repository gate
[`checks-20260927T123201044339Z.json`](checks-20260927T123201044339Z.json),
with lint, formatting, strict mypy over 36 source files, and `pip check` all
passing. The M7 quantitative reviewer also checked chart and table semantics.
The M6 formulas and golden outputs were not changed.

| File | SHA-256 |
|---|---|
| `src/quant_terminal/analytics/metrics.py` | `B8DA2CD837176D08A2625FC91E5B48197784AFAEAF06A72A7EAF4B39AF61656C` |
| `src/quant_terminal/analytics/__init__.py` | `F4D0BAD2E34B429727F76CB80764D3C46451D1E5BD70C2B6B79AD0291B123D12` |
| `tests/unit/test_analytics_metrics.py` | `F5FEB0E3F4716773F2436279179AB10F17169CC1BE3159B3877C3BFD4799D0ED` |
| `docs/m6-analytics-contract.md` | `6F2075A5093BDA89D52EF794B07179753810D186135A2E4F2C737A2C4DD7264C` |
| `docs/validation/checks-20260927T112815213791Z.json` | `23D2C7E9D21C0BA6EA0984A531E11EAC9CC7AEAD8718C0305C5267FCCC5F91A6` |
| `docs/validation/checks-20260927T123201044339Z.json` | `272CB1B413A1BD89E154ED16969B16FE0D62A37F3835225D78CE9D674C76457E` |

Limitations: MAE/MFE use observed NAV checkpoint close marks, so they do not
reconstruct intrabar extremes. Benchmark alpha/beta and related metrics remain
unavailable until a compatible currency and total-return benchmark series is
available. Risk of ruin is a model-conditional bootstrap estimate and is
unavailable without explicit assumptions. M6 performs research analytics only;
it does not submit broker or paper orders.
