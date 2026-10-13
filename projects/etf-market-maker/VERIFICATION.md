# Verification record

Fresh verification on 2026-09-22:

- `.venv-ml/Scripts/python.exe -m unittest discover -v`: 20 tests passed, including real PyTorch training, chronological split checks, future-input mutation tests, known-NAV/FX cases, inventory/quote direction, ledger reconciliation, atomic risk rejection, hedge depth cap, kill switch, cost and evidence rejection, committee veto/budget and full artifact generation.
- `.venv-ml/Scripts/python.exe run.py --seed 7 --events 1000 --epochs 10 --phase discovery --mode paper`: passed. 669 simulated fills; ARIMA-LSTM selected using validation data; strategy approval false; strategy capital allocated zero.
- Firm configuration verification: 45 unique agents, codex_local, absolute workspace paths, no dangerous bypass, no model override, correct reporting lines, instruction bundles and real CEO specialist IDs; paper-only owner configuration remains intact and read-only.

| Acceptance criterion | Status | Evidence |
| --- | --- | --- |
| Market-independent fair value and deviations | Confirmed | known value 100.25, deviation 0.08; market input rejected by NAV function |
| LSTM, Transformer, ARIMA-LSTM forecasting non-price targets | Confirmed | trained checkpoints and held-out metrics in run artifacts |
| Inventory and hedging | Confirmed | short 5000 => buy 4800 basket equivalents when liquidity/capital permit; ledger tests |
| Dynamic bid/ask prices | Confirmed | long/short skew, volatility widening, tick rounding and risk-side suppression tests |
| VaR/ES and named/liquidity stresses | Confirmed as synthetic models | risk.json, stress.json; tail uncertainty documented |
| Strategy Killer | Confirmed | missing evidence, leakage, regime, costs and capacity reject; tests exercise each |
| Capital allocation with binding vetoes | Confirmed | rejection => zero; caps and budget tests |
| Real market edge, GBP100m capacity and six-month/30-day qualification | Unverified; not claimed | no real holdings/order-book dataset or elapsed paper history |
| Interactive browser visual inspection | Unverified; not claimed | standalone HTML and linked artifacts generated and checked by integration tests |

The engine has no live execution path. Role handoffs are deterministic software records linked to Paperclip IDs, not assertions of independent LLM committee deliberation. README and WORKFLOW describe how the configured agents can independently review future real-data research.
