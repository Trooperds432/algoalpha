# Advanced asset accounting and research

Open **Advanced Assets** in the local terminal, choose a model, edit its synthetic
parameter example, and choose **Calculate asset model**. These asset-specific
simulations are separate from the existing equity/ETF backtest and paper engine.
They expose explicit accounting/model conventions; outputs cannot activate orders.

The same request works through CLI and API:

```powershell
uv run quant advanced-assets --request examples/asset-futures-settlement.json
```

POST `/advanced-assets` accepts exactly `operation` and `inputs`; GET
`/advanced-assets/capabilities` lists supported operations. Monetary values, rates,
days and futures multipliers are decimal strings. Contract counts and option
multipliers are JSON integers. Arrays are input-ordered and bounded to 256 rows;
currency maps are bounded to 64. Decimal coefficients have at most 34 digits and
exponent magnitude 18; model-specific limits can be narrower. Unsupported fields
and modes reject explicitly.

| Operation | Principal inputs | Output convention |
| --- | --- | --- |
| multi_currency_account | base_currency, initial_balances, fx_rates, events | native cash, native FIFO positions/PnL, translated NAV and total base PnL |
| fx_conversion | source_currency, target_currency, quantity, rate; optional fee | source debit including source fee, target credit |
| borrow_availability | requested, available | supplied borrow approval/shortfall |
| borrow_fee | short_quantity, price, annual_rate, days; optional day_count | short market-value simple fee |
| financing | cash_balance, debit_rate, credit_rate, days; optional day_count | simple debit charge/credit income and signed cash change |
| futures_pnl | contracts, multiplier, entry_price, exit_price | signed contract/multiplier mark change |
| futures_margin | contracts, multiplier, settlement_price, initial_margin_per_contract, maintenance_margin_per_contract, equity | collateral requirements, breach and top-up diagnostic |
| futures_settlement | contracts, multiplier, settlement_prices, initial_cash, initial_margin_per_contract, maintenance_margin_per_contract | daily cash variation, final cash, no notional purchase debit |
| continuous_contract | prices, contract_ids, roll_old_prices; optional method | raw/additive back-adjusted research series and rolls |
| option_price | option_type, spot, strike, time_to_expiry, volatility, risk_free_rate; optional dividend_yield, style | European theoretical price and unit-labelled Greeks |
| option_payoff | option_type, spot, strike, contracts, multiplier, premium | signed terminal gross payoff/net premium-adjusted PnL |
| option_margin | option_type, spot, strike, premium, contracts, multiplier; optional policy | long premium or uncovered-equity single-leg research margin |

## Multi-currency event replay

Account translation rates mean **base units per native unit**, with the base rate
equal to one. FX conversion rates separately mean **target units per source unit**.
Missing currency quotes reject. Each ordered event has a unique `event_id` and `kind`.
The replay copies its initial balances/rates, so rejection exposes no partial account
state and never mutates caller inputs. It is an in-memory native simulation, not a
persistent live account.

- `trade`: asset_id, asset_class (`equity`/`etf`), currency, signed quantity, positive
  price, nonnegative fee, borrow_available. FIFO lots realize native price PnL;
  all trade fees expense immediately. A sell's total resulting short exposure must
  not exceed its explicitly supplied borrow availability.
- `mark`: asset_id, nonnegative price. A zero terminal equity value is supported.
- `fx`: source_currency, target_currency, positive quantity/rate, nonnegative fee.
  Source cash must cover quantity plus fee; target receives quantity times rate.
- `cash`: currency, signed quantity. External flow is translated at event-time FX.
- `expense`: currency, nonnegative quantity. Applies a cost to native cash and PnL,
  without treating it as an external contribution. Carry-cost outputs can be applied
  explicitly through this event.
- `fx_rate`: currency, positive rate. Revalues native holdings/cash; the base rate
  cannot change from one.

Total base PnL equals final base NAV minus initial NAV minus event-time-translated
external flows. It includes FX revaluation and conversion quote/fee effects.
Native realised PnL reports FIFO trade PnL, immediate trade fees and expense events;
it is not a full attribution of FX trading/revaluation. Negative cash identifies
financing exposure and does not imply an approved credit line. Cash-flow timing,
settlement lag and FX lot-tax accounting are unsupported.

## Futures

The first settlement mark is the opening reference, with zero initial variation;
each next mark banks signed contracts × multiplier × price change in native cash.
The module neither debits full notional nor subtracts posted collateral from account
equity. Margin amounts are supplied per-contract inputs; initial cash must meet
initial collateral. A maintenance breach reports the top-up needed to reach initial
margin, with `action_taken=false`. Later supplied marks remain hypothetical even
after a call; no automatic top-up or liquidation is assumed. Daily cash variation
and collateral requirements follow [CME conventions](https://www.cmegroup.com/clearing/files/CME-Money-Calculations-Futures-and-Options.pdf).

Contract counts are signed integers; multipliers are positive Decimals. Negative
commodity futures prices are supported. There is no physical delivery, broker
margin feed or intra-day liquidity funding simulation.

Continuous-contract rows must be chronological and caller aligned. On each ID
change, `roll_old_prices` supplies the old contract price at the same instant as
the new contract quote; it is zero for other rows and the first row. Difference
back-adjustment adds subsequent new-minus-old gaps to prior history. Raw quotes
and roll diagnostics remain visible; adjusted history is marked `tradable=false`.
The module does not infer expiry/volume roll rules or turn adjusted prices into
fills. Ratio adjustment rejects.

## Options

European Black-Scholes assumes positive spot, strike, volatility and time-to-expiry,
with continuous annual risk-free/dividend rates. `style=american`, exotics and zero
expiry/volatility pricing reject; use terminal payoff at expiry. Payoff accepts zero
terminal spot, signed integer contracts and explicit positive integer multiplier.
Premium is per underlying unit and is paid by longs/received by shorts.

Greeks are per underlying unit: delta per spot unit, gamma per squared spot unit,
theta per year, vega per 1.00 absolute volatility change, rho per 1.00 absolute rate
change. Divide theta by the intended calendar basis and vega/rho by 100 for one
percentage-point scenarios; these are sensitivity estimates, not exact price moves.
The Decimal Hastings normal CDF approximation documents absolute error bound
7.5e-8; option price/Greek fixtures use tolerances rather than pretend ledger exactness.
See [OIC model definitions](https://prd-web.optionseducation.org/advancedconcepts/black-scholes-formula)
and [Greek definitions](https://prd-web.optionseducation.org/advancedconcepts/understanding-options-greeks).

`policy=equity_strategy_based` reserves long premium or estimates uncovered single-leg
U.S. equity short margin: premium plus the larger of 20% spot minus OTM amount and
the 10% floor (spot for calls; strike for puts), multiplied by absolute contracts
and multiplier. This uses the [Cboe strategy reference](https://www.cboe.com/us/options/strategy_based_margin)
and is not an account quote or complete regulatory margin calculation. Covered,
index, netted portfolio margin, early exercise, assignment/delivery and implied
volatility fitting reject or remain unsupported as stated in the capability contract.

Borrow fees and financing use simple annual accrual, with explicit 360/365/366 basis,
supplied rates and elapsed days. No compounding or implicit short-proceeds interest.
