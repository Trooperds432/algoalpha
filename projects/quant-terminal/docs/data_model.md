# Data and experiment model

Immutable schema-versioned records; canonical UTF-8 JSON metadata with decimal
numbers serialized as strings and UTC timestamps in ISO 8601. Analytical Parquet
tables use explicit decimal/timestamp schemas, never implicit float conversion.

| Record | Required fields and integrity |
|---|---|
| Instrument | stable instrument_id, symbol history, currency, asset class, exchange/calendar, timezone, tick/quantity step, effective intervals |
| Bar | instrument_id, start, end, available_at, OHLC, volume, frequency, source; unique instrument/frequency/start/version |
| CorporateAction | action_id, type, instrument, known_at, effective_at, ratio or amount, ex/pay dates, version |
| Universe | membership effective interval, known_at, delisting history and source |
| DatasetSnapshot | hash, schema/calendar versions, interval, source/license/entitlements, raw/adjusted/total-return flags, PIT/survivorship claims and evidence |
| OrderEvent | immutable event/order/intent IDs, sequence, status, timestamp, quantity, prices, model and risk reason |
| Fill | unique fill_id/order_id, signed side/quantity, reference/fill price, fee, slippage, timestamp, model version |
| LedgerEvent | event_id, fill/action link, cash delta, lot/quantity delta, receivable delta, currency |
| PortfolioSnapshot | time, cash, receivables, positions/marks/stale flags, NAV, exposures |
| MatchedTrade | deterministic trade_id, entry/exit fill allocations, quantity, costs, net P&L, duration |
| Metric | name, value or null, status/reason, formula/version, sampling/tolerance/conventions |
| ExperimentManifest | run ID, strategy/source hash, Git commit or explicit uncommitted marker + tree hash, engine/Python/dependency/platform versions, data snapshot, all parameters/assumptions, seed, dates, initial capital, created time |
| Artifact | run ID, relative path, media/schema, checksum, byte count, completion state |
| AgentInteraction | authenticated actor, role, issue/run IDs, timestamp, operation, outcome and evidence artifact; no raw credentials |

Data validation rejects NaN/infinity, nonpositive prices, negative volume, impossible
OHLC, duplicate/out-of-order records, unknown timezones or missing required metadata.
Complete-loss/negative-NAV fixtures are analytics-only initially; later explicit
delisting/write-off ledger events can represent zero recovery. Zero executable prices
remain invalid and are not a substitute for such events.
Missing calendar bars produce diagnostics; neither prices nor volume are silently
filled. Stable symbol IDs avoid conflating symbol reuse. Corrections create a new
snapshot. All selected rows satisfy available_at <= current time and universe known_at.

Write run artifacts to unique staging, checksum and atomically finalize. SQLite tracks
status and searchable indexes; a SUCCEEDED row is written only after durable artifact
completion. Append status events for recovery. Content-address source/data snapshots
are read-only inputs; comparison excludes generated run IDs/timestamps from economic
reproducibility checks. Never claim external vendor data PIT without evidence.
