# M20 advanced asset acceptance

**Gate: PASS.** Independent review and final frozen full-project checks passed.
`uv run python scripts/check.py`: **605 passed, 32 skipped**, one existing Starlette
test-client deprecation warning; Ruff lint/format, strict typing (93 source files)
and dependency checks clean. Evidence:
`docs/validation/checks-20261002T204504269020Z.json`.
Thirty skips are opt-in M16 hostile-container tests and two are Windows symlink
limitations; they do not constitute a passed M16 security gate.

| Owner requirement | Public implementation | Independent evidence |
| --- | --- | --- |
| Multi-currency accounting | money.multi_currency_account | native FIFO lots/fees, translated NAV, event-time external flows, FX revaluation, total loss |
| FX | money.fx_conversion and account FX events | explicit quote-direction/debit-credit/fee fixture and native cash |
| Futures | futures.futures_pnl / futures_settlement | signed long/short/negative-price variations, daily cash telescoping, zero notional debit |
| Continuous contracts | futures.continuous_contract | explicit same-time roll quotes, raw preservation, additive historical gaps, non-tradable output |
| Contract multipliers | futures PnL/settlement; option payoff | asset-specific quantity/multiplier fixtures and integral-contract validation |
| Futures margin | futures.futures_margin / settlement | initial admissibility, maintenance breach/top-up, no automatic funding |
| Borrow availability | money.borrow_availability and account short gate | supplied quantity shortfall, insufficient-borrow rejection, short-cover/crossing FIFO |
| Short-borrow fees | money.borrow_fee | exact simple market-value/day-count fee, explicit expense application |
| Financing | money.financing | debit/credit simple accrual and signed cash change |
| Options | options.option_price / payoff | independent textbook European price fixture, dividend parity and zero-underlying terminal payoff |
| Greeks | options.option_price | finite-difference sensitivities with explicit units and approximation tolerance |
| Options margin | options.option_margin | long premium and Cboe-reference short call/put fixtures, unsupported policy rejection |

M20 has twelve shared operations exposed at POST `/advanced-assets`, GET
`/advanced-assets/capabilities`, CLI `quant advanced-assets --request ...`, and
the **Advanced Assets** workspace. Integration loads the exact UI example JSON
and runs every operation through API and CLI against independent economic values.
Legacy `InstrumentMeta` still rejects fx/future/option, preserving equity-only
execution assumptions. Native account replay explicitly limits its trade events
to equity/ETF; derivatives use their own supplied simulations.

Fresh focused suite: **67 passed** (14 money, 19 futures, 20 options, 14 integration),
with one existing Starlette test-client warning. Frontend build passed; 12 existing
frontend tests passed. The build retains the known large Plotly chunk warning.
Fresh browser discovery returned no apps or browsers; visual UI smoke is unverified.

No external connection, order, strategy forward test, real account data or secret
was used. No physical delivery/assignment, American/exotic exercise, implied-volatility
fitting, broker live margin, persistent multi-currency account, or automatic margin
funding is implemented. Supported mathematical/accounting conventions and unsupported
modes are explicit in `docs/advanced-assets-usage.md` and the milestone contract.
M16 remains unpassed. Paperclip role plans are not counted as actual worker interaction;
this gate records real collaborating-agent implementation/review and CLI/API exercises.
