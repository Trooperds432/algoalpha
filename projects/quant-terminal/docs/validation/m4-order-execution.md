# M4 order management and execution gate

Status: **PASSED** on 2026-09-26.

The historical broker now validates order intents, records lifecycle events,
and emits immutable fills plus a terminal execution `Trade` aggregate. Market,
limit, stop, and stop-limit orders use a documented OHLC fill model. Commission,
slippage, and matching behavior are replaceable interfaces with deterministic
default implementations. Bar volume is shared across orders in acceptance-time
and OrderId priority, with at most one fill per order per bar. Limit protection,
partial fills, cumulative per-order minimum commission, time-in-force, gaps,
stop-limit next-bar activation, and causal observation timestamps are covered.

The focused execution suite passed **26 tests**. The fresh complete gate is
[`checks-20260926T134752537632Z.json`](checks-20260926T134752537632Z.json):
**102 tests passed**, Ruff lint and formatting passed, strict mypy passed, and
`pip check` found no broken requirements. An independent read-only quantitative
execution review passed on the hashes below and verified matching, shared
capacity, adverse tick rounding, fee calculation, lifecycle, and M3 fill
handoff.

| File | SHA-256 |
|---|---|
| `src/quant_terminal/execution/simulator.py` | `6288329413A0D50F1A7D17D8023F7C9DE45FD060D4B2118519963E8EFA28B737` |
| `src/quant_terminal/execution/fills.py` | `50CF9744A6E1AD543C029A469FC9A8C15B539F6A24CC0643D0C0542D3948BDC3` |
| `src/quant_terminal/execution/models.py` | `47542D36DC3FE180352291C71F1824DE37B9D1F0A79CFCD6DF760D8A991FC868` |
| `src/quant_terminal/orders/lifecycle.py` | `3F0E54E6030283D2FC00F0721DBC83B63FC78B995C0099965669A6AB6B64E764` |
| `src/quant_terminal/orders/models.py` | `CA2FD619DCD22ED806EA9DFDA0D070D3C5E43196D19A84F010608EE87AC642CA` |
| `tests/unit/test_historical_execution.py` | `E2F43E44EFB2EC8BD957C63078F8721986B74317C3E8CF9EF86A8F8CD6595326` |

Limits: the broker is local and bar-based. One run may use one frequency per
instrument. It does not model exchange queues, intrabar sequencing, order-book
depth, or real-world fill certainty. A `Fill` carries both economic bar time
and observation time; downstream code must not apply it before it is observable.
No external broker adapter or order submission is included.
