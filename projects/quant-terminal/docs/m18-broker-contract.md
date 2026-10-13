# M18 broker architecture contract

The owner explicitly requested M18 on 2026-10-01 after independent M17 paper
approval. This schedules broker architecture work; it does not change AGENTS.md's
authorization boundary: no live connection or order is authorized. M16 also remains
in progress and unpassed.

Trading 212 is the sole M18 provider selected from the owner's earlier explicit
Trading 212 environment preference. Target is the Demo base URL only. Architecture
must be testable with a scripted transport that performs no DNS or network calls.
Do not add a real HTTP transport, secret configuration UI, actual account probe or
broker order submission route in this milestone. Auth and protocol mapping are pure
testable modules. No real Trading 212 credentials may be read or stored. Tests use
synthetic values only.

Official API status and constraints are recorded at
`https://docs.trading212.com/api/section/general-information/api-limitations`
and `https://docs.trading212.com/_bundle/api.json?download=`. The public v0
reference labels its API beta, limits support to Invest and Stocks ISA, executes
orders only in primary account currency, and requires a key/secret pair using HTTP
Basic authentication. It documents per-account and per-endpoint rate limits.
Trading 212 order creation is not idempotent; a network timeout is ambiguous. The
system must retain intent/reservation, halt duplicate submission and reconcile
against broker order/fill/position state before releasing risk. Provider sells use
negative quantities. Fill exposure remains reserved until a positions reconciliation
records the executions. Schema/rate assumptions must be versioned and rechecked before
any future runtime activation.

The adapter schema was reviewed against Trading 212's OpenAPI 3.0.1 v0 Beta bundle on
2026-10-02. It uses the fixed Demo root and these published per-endpoint budgets:
account summary 1 request/5s; positions 1/1s; pending orders 1/5s; limit order 1/2s;
historical orders 20/1m. The checked-in reference snapshot is
`docs/references/trading212-openapi-v0-20261002.json` (SHA-256
`A272F70A713FA9F2F906E9B4BE12136C9D5D63D7F9F48562E939AA35C4FC041D`). The positions
request is `/equity/positions`. The adapter provides typed request descriptions and
strict parsers only. Snapshot reconciliation is exact in account currency and position
quantity, with mismatches retained as mismatches. This milestone has no rate scheduler,
network transport, or live fallback.

M18 must provide one typed adapter contract; auth abstraction; validated supported
order/status/fill mappings; account and position reconciliation; heartbeat/stale
blocking; endpoint rate budgets and Retry-After; persistent duplicate/ambiguous-order
protection; pre-trade limits for position, order, gross/net exposure, leverage,
daily loss, drawdown and stale market data; and a kill switch. Every refused order
must be refused before transport. Unknown provider status, instrument, currency,
fill identity, timestamp or schema fails closed. No successful HTTP response alone
proves an order fully executed.

M18 UI/API must display broker architecture, Demo and Live modes visibly distinctly.
Demo displays architecture-only; Live is unavailable. Both report external
connectivity/orders disabled, with the reason explicit. No adapter can select a Live
URL. The durable kill switch is available in the pretrade risk module; authenticated
HTTP/UI operator control is not exposed while the broker API is disabled. A future
activation requires a separately authorized scope and a new tested
security/reconciliation review.

Gate: fake-transport contract fixtures and independent wire-shape review; exact
financial/reconciliation tests; kill/risk/rate-limit/idempotency/restart tests;
strict no-network/no-secret boundary checks; terminal UI distinct-mode check where
available; full suite and independent review. M17 passing is the prerequisite. M16
must still be resolved before any arbitrary strategy enters broker workflow.
