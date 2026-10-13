# M20 advanced asset classes

Owner requested M20 after M19 passed. The core single-currency equity/ETF ledger
retains its existing contract. New asset-specific local accounting/research modules
expose explicit FX, futures and European option conventions through a shared API,
CLI and Advanced Assets workspace. They use supplied synthetic inputs; they do not
enable external broker operations or strategy forward testing. M16 remains unpassed.

Multi-currency: independent native-currency cash accounts, FIFO equity lots and
native realised PnL, explicit cash flows, marks, FX conversions and base-currency
translation. Snapshot FX quotes are base units per unit of native currency; the
base quote must equal one. Conversion quotes separately mean target currency per
source unit. FX conversion fees debit source cash. External cash-flow translations
are booked at event-time quotes. FX revaluation contributes to total base PnL;
native realised PnL remains separately labelled. Missing currency quotes reject.
Events are processed atomically by an isolated account replay, with unique event IDs.
Negative cash represents financing exposure, not approved credit. Shorts require
explicit borrow availability; no inferred locate or borrow source.

Futures: integral signed contract quantities, explicit positive multipliers,
daily variation settlement banked in cash, supplied per-contract initial and
maintenance margins, explicit margin-call diagnostics. No notional purchase cash
debit. Negative futures prices are supported. Continuous contracts use explicit
chronological raw prices/IDs and same-time old quotes at rolls; difference
back-adjustment is a research series, not a tradable instrument. No inferred roll
schedule, physical delivery, exchange margin feed or ratio adjustment.

Borrow/financing: explicit supply checks, annual simple fees with a supplied day
count convention, short market-value borrow charges and debit-cash interest.
Rates, days and available quantities are inputs, not broker guarantees. No compounding
or implied proceeds interest; credit interest is separately specified.

Options: European Black-Scholes with continuous dividend yield; supported positive
expiry/volatility interior has delta/gamma/theta/vega/rho with labelled per-year and
per-unit sensitivities. Expiry payoff/PnL uses signed integral contracts and an
explicit multiplier. Numerical normal-CDF tolerance is documented and independently
tested; exact Decimal ledger arithmetic is distinct from approximate option valuation.
American exercise, exotic payoff, physical assignment, portfolio/netted/index margin
and implied-volatility fitting reject. Uncovered single-leg equity strategy margin
is a research policy using the Cboe 20%/10% reference, not an account margin quote.

All monetary parameters are bounded finite Decimals, supplied as strings in JSON;
contract counts and option multipliers are bounded JSON integers.
Fixed precision34/half-even contexts isolate ambient precision, rounding and traps.
Unsupported modes reject; legacy backtest/paper/broker routes must not accept these
new classes through equity assumptions. Asset-specific native simulations are
accessible independently with clearly labelled conventions.

Gate: independent FX/multi-currency FIFO and cash-flow fixtures, futures settlement/
roll/margin fixtures, borrow/financing fixtures, option price/parity/Greek/margin
fixtures, boundary/unsupported-mode tests, API/CLI integration, frontend build/tests,
fresh full project checks and independent review. Paperclip assignments alone do not
prove employee participation or terminal interaction.

Primary sources: CME daily cash settlement/margin conventions,
https://www.cmegroup.com/clearing/files/CME-Money-Calculations-Futures-and-Options.pdf;
https://www.cmegroup.com/education/articles-and-reports/understanding-margin-changes;
Cboe equity strategy margin,
https://www.cboe.com/us/options/strategy_based_margin;
Options Industry Council model/Greek definitions,
https://prd-web.optionseducation.org/advancedconcepts/black-scholes-formula and
https://prd-web.optionseducation.org/advancedconcepts/understanding-options-greeks.
