# Local paper sessions

M17 provides virtual accounting and simulated OHLC fills. No market-data vendor,
Trading 212 endpoint, exchange or external broker is connected. The implementation
does not automatically create, start or forward-test a strategy. Sessions begin
disconnected and must explicitly reconcile before accepting observations.

Start the existing terminal with `quant --state-dir <outside-project-directory> serve`
and open `/ui/`. Choose **Paper Trading** to create a disconnected USD ETF
buy-and-hold configuration, inspect virtual cash/NAV/orders, reconcile the virtual
broker, or disconnect. The form displays its fees, slippage, participation and
staleness policy. Other built-ins/configurations are available through API/CLI.

## API

All endpoints inherit the loopback Host, same-origin, JSON and body-size controls.

| Method | Path | Purpose |
|---|---|---|
| GET | `/paper/sessions` | List reconstructed paper status |
| POST | `/paper/sessions` | Create `{session_id, config}`; example below |
| GET | `/paper/sessions/{id}` | Read exact portfolio, orders and reconciliation snapshot |
| POST | `/paper/sessions/{id}/reconnect` | `{event_id, snapshot}`: explicitly acknowledge a matching virtual broker snapshot |
| POST | `/paper/sessions/{id}/disconnect` | `{event_id, reason?}`: stop fills and proposals, retain state |
| POST | `/paper/sessions/{id}/events` | `{event_id, sequence, bar}`: push one completed observable bar |
| POST | `/paper/sessions/{id}/fills` | `{fill}`: acknowledge an exact already-generated fill; never inject new executions |

Copy `reconciliation` from current status into a reconnect request. It contains
cash, nonzero positions, order status/filled/remaining quantities, fill IDs and the
last durable source sequence. Mismatches halt instead of changing balances.

A source adapter supplies a stable unique `event_id` and contiguous sequence
starting at 1, preserving these identities across retries/restarts. Each `bar`
uses exact decimal strings for prices/volume and aware ISO timestamps:

```json
{
  "event_id": "source-event-1",
  "sequence": 1,
  "bar": {
    "instrument_id": "ETF-1", "frequency": "1m",
    "start": "2026-10-01T14:30:00+00:00",
    "end": "2026-10-01T14:31:00+00:00",
    "available_at": "2026-10-01T14:31:00+00:00",
    "open": "100", "high": "102", "low": "99", "close": "101",
    "volume": "10000", "source": "synthetic-example"
  }
}
```

The fixed timestamps above illustrate the schema; submitting them later will
correctly reject as stale. A strategy sees only accepted, completed, as-of-visible
history. Orders generated from this observation cannot fill on the same bar.
The next eligible bar uses the existing commission, slippage and volume model.

## CLI

`examples/paper-session.json` contains a configuration example. Financial values
are decimal strings. No sample is automatically submitted by installation.

```powershell
quant --state-dir C:\path\outside-project paper-create --request examples/paper-session.json
quant --state-dir C:\path\outside-project paper-sessions
quant --state-dir C:\path\outside-project paper-status my-paper-session
quant --state-dir C:\path\outside-project paper-command my-paper-session reconnect --request reconnect.json
```

Every separate CLI invocation is a fresh session owner and requires reconciliation.
For an event invocation, supply `--reconciliation reconnect.json` along with
`--request event.json`. This performs the explicit acknowledgement and observation
in the same process. Use a new reconnect event ID and the current snapshot. A
long-lived API service keeps its in-process acknowledgement until disconnect/fault.

## Recovery and limits

State is stored as `<state-dir>/paper/<session_id>.sqlite3`. Preserve this entire
file. SQLite commits each command with its accounting/order projection, chained
integrity hash and journal head. Recovery never invokes strategy callbacks; a
process restart reconstructs the same economic state and requires reconciliation.
Do not edit journal rows to repair state. Corruption and missing committed tails
reject explicitly. Hashes detect accidental corruption, not an attacker who can
rewrite the database and all hashes.

Missing sequences, changed duplicates, old/future/stale observations and conflicting
fill acknowledgements halt processing. No fabricated fills bridge missing data.
After checking virtual broker state, reconnect and supply the missing observations
in source order while still fresh. If historical gaps are stale, start a separately
configured, reconciled session rather than silently filling at invented prices.
Staleness is checked on event admission before any fill or strategy callback;
there is no background feed heartbeat or autonomous vendor subscription.

Initial scope: one equity/ETF, one currency, long-only, the three existing built-in
strategies, fixed declared execution settings, and 10,000 durable commands per
session. Custom Python and third-party dependency execution remain unavailable.
These are bar-based paper fills, with no exchange queue realism claim.
