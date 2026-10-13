# Paperclip desk workflow

Company: AlgoAlpha. Project: Multi-Agent ETF Market Maker. Build/verification issue: ALG-2.

The CEO assigns bounded child issues with an input artifact, requested output and acceptance criteria. Specialists check out their assigned issue and comment with artifact paths and limitations. Do not run several writers against the same research files or shared TradingView chart.

1. **Data Engineer:** provide timestamped basket and order-flow data, provenance, permitted-use notes and validation failures. Weight-only inputs are insufficient.
2. **Quant Researcher:** price the basket without seeing ETF market price; then analyse deviations and past-only mean reversion. Document costs and reject apparent edges smaller than costs.
3. **ML Researcher:** train the three models and persistence baseline; use chronological purged splits. Predict spread/depth/volatility only. Preserve failed models.
4. **Trader:** propose inventory skew, hedge triggers, gross/net limits and quote policy. Use engineering defaults only for synthetic testing; request owner decisions for material real-market limits.
5. **Risk Manager:** independently inspect drawdown, loss, exposures, VaR/ES and stress scenarios. Authorise only a bounded paper simulation when evidence permits. Retain direct escalation to Board.
6. **Execution Agent:** run the costed simulator, preserve quotes/fills/rejections/equity. No live tools or accounts. A software engineering smoke test is not trading-strategy approval.
7. **Validation Agent / Strategy Killer:** attack leakage, future timestamps, split overlap, regime coverage, cost sensitivity, queue assumptions and GBP100m capacity. Missing required evidence means rejection.
8. **Portfolio Manager:** collect Quant, Fundamental, Risk, Execution and Validation votes. Risk/Validation rejection or missing votes means zero allocation. Reduce-size votes require explicit caps. No live capital deployment.
9. **Reporting Agent:** publish an internal artifact with every limitation and failed gate. Reconcile PnL to the ledger. State exactly which evidence is synthetic, historical or observed paper trading.

The deterministic runner is the common calculation tool for these roles. It does not replace independent human/agent evidence review or authenticate approval records. Start new research issues only with bounded scope; there is no perpetual autonomous trading loop.
