# M2 historical data contract and test design

Scope: equities and ETFs, XNYS daily and one-minute OHLCV bars. Every timestamp
is timezone-aware and normalized to UTC. Bar interval is `[start, end)` and
`available_at >= end`; the source's publication delay is explicit. Prices and
volume are finite Decimal values; executable prices are positive, volume is
nonnegative, and high/low bound open and close. Instrument metadata fixes stable
ID, symbol, currency, asset class, exchange calendar, IANA timezone, tick and
quantity step. A correction produces a distinct immutable dataset snapshot.

Import CSV or Parquet with an explicit column schema. Reject duplicate or
out-of-order `(instrument_id, frequency, start)` rows before sorting; never
silently repair. Missing scheduled bars yield diagnostics and remain missing.
Calendars determine holidays, early closes and daylight-saving session times.
The XNYS implementation uses the pure-Python `holidays` package's PUBLIC and
HALF_DAY categories plus `zoneinfo`/pinned `tzdata` for 09:30 ET opens and
16:00/13:00 ET closes.
The initially selected `exchange_calendars` dependency could not load its pandas
binary under this workstation's Windows Application Control policy; it was
removed before finalizing the M2 dependency set. Calendar behavior is checked
against exchange-published fixtures, not inferred from US federal holidays.
The first implementation supports one instrument/frequency per snapshot and
rejects other calendars/frequencies explicitly. Decimal Parquet fields use a
documented precision/scale, with out-of-range values rejected rather than
rounded; timestamps use UTC microseconds. A snapshot manifest records input
hash, canonical content hash, schema/calendar/library versions, source,
publication delay, raw-price convention and row count. Parquet and manifest are
written to unique staging and finalized without overwriting prior snapshots.

The reader receives a simulation clock and exposes bars only when both `end`
and `available_at` are no later than clock time. A request whose end is in the
future raises FutureDataError; no direct unrestricted table escapes through the
strategy-facing API. DuckDB queries are parameterized and scoped to the
immutable Parquet snapshot. Internal research queries can examine the full
snapshot but must not be presented as strategy-visible history.

Implemented API: `DatasetStore.import_csv/import_parquet` return a
`DatasetSnapshot`; `load_snapshot` verifies the manifest and Parquet checksum;
`query_bars` is explicitly internal and unrestricted by simulation time.
`DataFeed.between(start,end)` and `DataFeed.history(end,count)` accept UTC-aware
times and require `end <= SimulationClock.now`. Returned bars additionally
require `bar.end <= end` and `bar.available_at <= end`, so an as-of query made
after the clock has moved forward cannot see values unpublished at its
requested historical end. This Python API is for
trusted strategies; M16 must provide adversarial isolation before untrusted
strategy code can run.

Corporate actions are represented by an immutable schema with `known_at` and
`effective_at` and explicit raw/adjusted price mode. M2 stores metadata and
validates timing; adjustment and cash-credit logic belong to M3/M5. A dataset
with adjusted/total-return prices cannot later receive unqualified cash actions.

Gate fixtures: reproducible synthetic XNYS daily data across a holiday and an
early close; duplicate, missing, out-of-order, invalid OHLC, negative volume,
timezone conversion and future access failures; CSV/Parquet round trips with
matching canonical hashes; a query at bar end before publication must not see
the bar. No external vendor data or trading claim is attached to these fixtures.
The packaged example is `examples/data/synthetic_xnys_2026.csv`, exercised by
`examples/m2_known_dataset.py`. Missing-bar diagnostics cover the explicit
interval, which import currently bounds by the first and last supplied bars;
leading/trailing absences outside that interval require a caller-declared
expected range in a later extension.
