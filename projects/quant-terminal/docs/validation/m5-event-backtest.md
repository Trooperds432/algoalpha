# M5 event-driven backtest gate

Status: **PASSED** on 2026-09-27.

The event engine connects immutable point-in-time data snapshots, deterministic
strategies, pre-trade risk checks, the historical broker, M3 accounting, and
immutable result records. Each event is processed at its observable
`available_at` time. Existing eligible orders execute before completed-bar
callbacks; newly emitted intents cannot fill on the signal bar. The restricted
strategy context rejects history requests past the simulation clock.

`BacktestResult` exposes NAV checkpoints and derived cash, positions, returns,
exposures, order/fill/trade records, commissions, slippage, optional benchmark
series, and sorted run metadata. The deterministic examples are BuyAndHold,
MovingAverageCross, and SimpleMomentum. The hand-calculated buy-and-hold fixture
buys 10 shares at 100.25, pays 1.00 commission, marks at 100.50, and reconciles
to cash 8,996.50 and NAV 10,001.50 with a 0.00015 checkpoint return.

Cash-constrained buy orders reserve their maximum execution price and fees.
Market and buy-stop orders require an explicit tick-aligned maximum execution
price. The engine checks the full requested quantity against the notional limit
at admission and checks each proposed fill against its price cap, cumulative
order notional, cash, and other active buy reservations. Rejected or expired
terminal orders release reserves.
Zero costs require `allow_zero_costs=True` and are recorded as
`ZERO_COST_ASSUMPTION`; custom zero-cost models declare `is_zero_cost = True`.
Benchmark output is explicitly normalized price return and excludes dividends.

The focused backtest and execution suites passed **43 tests**. The fresh full
gate is recorded in
[`checks-20260927T104507782624Z.json`](checks-20260927T104507782624Z.json):
**119 tests passed**, Ruff lint and formatting passed, strict mypy passed, and
`pip check` found no broken requirements. An independent read-only M5 review
passed the same revision, including causality, fill caps, reservations, cost
labels, accounting handoff, benchmark convention, and the regression fixtures.

| File | SHA-256 |
|---|---|
| `src/quant_terminal/backtesting/engine.py` | `CECAD825B70E751FEF53CB0C26F39E0EDE1D3D5E348EEF45909E01BBA5A6F16E` |
| `src/quant_terminal/backtesting/models.py` | `97EA279AF8C6944B61066DE0D120F07363387B6C72067E085E18676F1B3FB45F` |
| `src/quant_terminal/backtesting/strategies.py` | `DC36B25B797A69030CFC2CC7E7F783C92799228DF11355A3513CEDFBFF6D84D4` |
| `src/quant_terminal/execution/simulator.py` | `BAF5AE3CB24F7A60FDBB98603FA8C55BB3ECD818A8FD6D6A4C6F57C26F694831` |
| `src/quant_terminal/execution/models.py` | `FD6E5B492378E03716221EEF317C6CC0AD82758B2A4A14578BE18F6E74F8243D` |
| `src/quant_terminal/orders/models.py` | `DFEF1973616FCF90EE4FFD4CD125BB9297F0F9EB4DEE1A39812CA0882F8F5AED` |
| `src/quant_terminal/orders/lifecycle.py` | `8A64AB935D3783FD56609566075B95B36688C3449C50E6F463591CA2CD6EBAC2` |
| `tests/integration/test_event_backtest.py` | `FEC39F5F9EC21B0EEDAFBC300346A4B8AF2913471E8D703791508E443A52CA23` |
| `docs/m5-backtest-contract.md` | `2BA31BD398CF84AD3E49A082959D52F65B63B1661352C38A657ABBE68AFD103F` |

Limits: strategies remain trusted in-process Python; M16 is responsible for
hostile-code isolation. The benchmark is price-only, no corporate-action cash
flows are applied by this milestone, and the execution model remains
deterministic OHLC rather than exchange queue simulation. M5 is research-only;
it does not connect to a broker or submit paper/live orders.
