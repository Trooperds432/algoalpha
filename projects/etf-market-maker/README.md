# Multi-Agent ETF Market Maker

A runnable **paper-only engineering desk** covering all seven requested deliverables. Uses an explicitly synthetic three-constituent ETF, not SPY, QQQ, TLT, GLD or a Bitcoin ETF feed. The £100m capacity target is evaluated as hypothetical scenarios, not certified capacity.

## Run

From PowerShell in this directory:

```powershell
.\.venv-ml\Scripts\python.exe run.py --seed 7 --events 1000 --epochs 10 --phase discovery --mode paper
.\.venv-ml\Scripts\python.exe -m unittest discover -v
```

Or double-click `Run-ETF-Desk.cmd`. Each run prints its new `runs/<UTC timestamp>-<id>/report.html` location. Open that HTML file locally. Old runs, including failures, remain intact. There is no live mode, broker adapter or API key requirement.

The project-local environment includes pinned NumPy and CPU PyTorch. To rebuild it:

```powershell
python -m venv .venv-ml
.\.venv-ml\Scripts\python.exe -m pip install -r requirements-ml.txt
```

## Seven deliverables

| Deliverable | Implementation | Evidence/output |
| --- | --- | --- |
| Independent fair value | `desk.fair_value` | `fair-value.json`, priced before market comparison |
| Order flow and forecasting | `flow_models.py` | LSTM, Transformer and ARIMA(1,1,0)-LSTM trained on synthetic event histories; validation-selected forecast; held-out MAE; saved weights |
| Inventory and hedging | `desk.hedge_decision`, `PaperLedger` | Signed ETF and basket positions, gross/net gates, fee/slippage ledger, short-5000/hedge-4800 example |
| Dynamic spreads | `desk.dynamic_quote`, `simulate` | Inventory skew, volatility/depth widening, tick rounding, one-event quote latency |
| Portfolio risk | `risk_governance.py` | 95%, 99%, 99.9% empirical VaR and expected shortfall; 10 directional hypothetical stresses |
| Strategy Killer | `validate_strategy` | Leakage, missing evidence, regime, net-cost, participation and phase-threshold checks; rejection reasons retained |
| Capital committee | `allocate_capital` | Quant/Fundamental/Risk/Execution/Validation votes; binding veto, explicit risk caps, total budget |

## Fair-value input

Weights alone cannot price an ETF. The supported method fixes constituent quantities implicitly from initial capital weights and reference prices:

`fair_value = base_nav × Σ(weight × price × fx / (reference_price × reference_fx)) + net_cash_per_share − accrued_fee_per_share`

`fx` is base currency per constituent currency. Each timestamp covers both its asset price and FX observation. A timestamp in the future, older than 60 seconds, an invalid price, duplicate symbol or weights not summing to one is rejected. The fair-value function rejects `market_price`; the separate `dislocation` function performs that comparison afterwards. This is an illustrative fixed-share basket model, not a substitute for issuer holdings, shares outstanding, distributions and creation-unit cash adjustments.

```json
{
  "base_nav": 100,
  "currency": "USD",
  "underlying_assets": [
    {"symbol":"A","weight":0.25,"price":100.4,"reference_price":100,"fx":1,"reference_fx":1,"timestamp":"2025-01-01T00:00:00Z"},
    {"symbol":"B","weight":0.25,"price":100.2,"reference_price":100,"fx":1,"reference_fx":1,"timestamp":"2025-01-01T00:00:00Z"},
    {"symbol":"C","weight":0.50,"price":100.2,"reference_price":100,"fx":1,"reference_fx":1,"timestamp":"2025-01-01T00:00:00Z"}
  ]
}
```

At the supplied timestamp this yields 100.25. Comparing it with a separately supplied market price of 100.33 yields a dislocation of +0.08. Estimated round-trip costs determine whether it is worth investigating; creation/redemption and borrow access are not assumed.

## Forecast protocol

- Forecast targets are next-event spread width, displayed liquidity and trailing realised volatility, **not price**.
- Chronological 60/20/20 raw data blocks; 12-event windows remain entirely within each block. Training-only scaling; validation-only checkpoint and model selection. Persistence competes with the neural models on the same validation metric.
- The chosen model's predictions enter quotes only for held-out targets. Earlier events use observed spread/depth and trailing volatility. Quotes submitted at event `t` face orders at `t+1`; forecast availability is checked.
- The hybrid uses conditional least-squares ARIMA(1,1,0) with drift and an LSTM residual correction. This is not a broad ARIMA order search.
- `models/*.pt`, training scales, ARIMA coefficients, selected epochs, raw synthetic events and source hashes are saved per run. Regeneration uses the seed and pinned environment.

## Execution assumptions

The USD1m starting balance is simulation bookkeeping. ETF fills have a capped stochastic arrival probability, 50% customer-flow queue participation and an event-depth cap. They execute at the prior quoted price; basket hedges pay adverse slippage. Every leg pays fees. Fixed random seeds allow repeatable comparisons. Cost sensitivity reruns the quoting policy, so fills may change as costs widen quotes.

ETF inventory and basket hedges remain separate. Hedge decisions are capped by displayed depth and gross capacity. Every fill passes atomic inventory, gross exposure, net exposure, loss and drawdown checks. Breach detection latches the desk halt; recovery requires a new reviewed run. The engine does not assume it can liquidate during a halt. End-of-run open positions are marked to market; stress tests separately model liquidation costs.

Borrow availability, financing, taxes, dividends, exchange priority, latency distributions and ETF creation/redemption are not implemented. The displayed PnL is **not** evidence of achievable performance. A few synthetic minutes cannot support a credible annualised Sharpe.

## Governance and organisation

`organisation.json` maps the ten project roles to real AlgoAlpha Paperclip IDs, plus the Fundamental committee adviser. Three missing roles were added: Validation Agent, Portfolio Manager and Reporting Agent. The CEO coordinates the project; existing firm reporting lines remain intact, including Risk's direct Board access. See `WORKFLOW.md` for issue handoffs.

The run's `agent-messages.jsonl` is a deterministic record of engine-stage handoffs, not a claim that LLM agents independently deliberated. Paperclip agents can use the engine and review the resulting artifacts. Model-generated trading authority is never inferred from these logs.

Owner thresholds are read from `../../config/risk-thresholds.json`: Discovery 1.0/25%, Promotion 1.5/15%, Production-paper 2.0/10%. The file is never modified. Synthetic data always fails promotion. Further paper eligibility requires at least six months real historical testing, documented out-of-sample costs, independent review and 30 actual days of paper observation. Simulated time does not count toward that duration.

The sample committee correctly allocates zero strategy capital because Risk and Validation reject promotion. Independent approval assertions must be backed by actual records; JSON flags alone do not authenticate evidence. GBP100m scenarios use GBP-denominated assumptions; the USD simulation is not silently converted to GBP.

## Sources and limits

- [SEC investor bulletin on ETFs](https://www.sec.gov/servlet/sec/investor/alerts/etfs.pdf): market price and NAV can differ; institutional creation/redemption is distinct from secondary-market trading.
- [Avellaneda and Stoikov, 2008](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf): inventory-dependent reservation-price motivation. The implemented quote policy is a disclosed heuristic, not a calibrated replication or an optimality claim.
- COVID, 2008, flash-crash and Volmageddon names label hypothetical stress magnitudes, not historical data. VaR uses one-second synthetic scenarios with no daily scaling. Tail-sample warnings are part of the output.

Real ETF research next requires dated holdings, licensed point-in-time quotes/order flow, corporate actions, data-availability timestamps, measured execution costs and capacity evidence. TradingView is not used as a substitute for a historical order-book dataset.
