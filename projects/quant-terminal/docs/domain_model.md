# Domain model and public contracts

All contracts below are specifications, not implemented classes. Validation errors
are typed failures, not silent data repair. Identifiers are immutable strings with
distinct RunId/StrategyId/OrderId/FillId/InstrumentId types. Generic immutable events
carry schema_version, event_id, economic event_time, observed_at, run_id and per-run
sequence. Market-data bars separately carry available_at, the first time their
contents may be queried. A market event's observed_at is no earlier than the source
bar's available_at; fills may retain an earlier economic time while delivery and the
simulation clock advance only at observed_at. The terms are not interchangeable.

| Contract | Input and output | Owner / failures |
|---|---|---|
| Clock | now(); advance(aware UTC) | engine; rejects backward time |
| DataFeed | history(instrument, end, count, capability) → immutable bars | availability checks; future access rejected |
| Strategy | initialize, on_bar, on_order_update, on_fill, on_end | no ledger writes; callback failure terminates run |
| StrategyContext | visible history, portfolio snapshot, submit(intent), cancel(id) | intent channel only; no broker handle |
| OrderIntent | instrument, side, quantity, type, limit/stop, TIF, client_intent_id | positive finite quantity; validated tick and currency |
| RiskManager | evaluate(intent, current snapshot, reservations) → accept/reject + reason | fail closed; never edits historical limits |
| Broker | submit/cancel/reconcile; immutable order events and fills | common historical/paper/live interface |
| ExecutionModel | eligible orders + next market event → proposed fills | respects timing and shared capacity |
| FillModel | order, bar, remaining capacity → quantities/reference prices | no same-decision-bar fills |
| SlippageModel | proposed fill + assumptions → adverse price/cost | constrained limit and valid positive price |
| CommissionModel | execution facts + cumulative order fees → fee delta | deterministic and independently testable |
| Portfolio | apply(fill/action/cash event) → immutable snapshot | single ledger writer; duplicate ID rejected/idempotent |
| Position | signed FIFO lots, units, cost basis, marks, stale flag | derived from events; no strategy mutation |
| BacktestEngine | manifest, capabilities, strategy factory → BacktestResult | FAILED on integrity faults |
| BacktestResult | typed tables + manifest/artifact references | no hidden mutable strategy state |
| Optimizer | IS capability, search space, objective, seed → all trials | OOS inaccessible; undefined metrics ineligible |
| WalkForwardEngine | frozen window plan → IS decisions + OOS results | overlaps/leakage rejected |
| MonteCarloEngine | sample, method, seed, path count → path metrics | records dependence assumptions and valid samples |
| MetricsEngine | BacktestResult + conventions → values + defined/status fields | undefined is null with reason, not zero |
| ExperimentRepository | create immutable manifest/artifacts; query/rerun/compare | conflict on overwrite, atomic completion |
| ReportGenerator | result + metrics → escaped HTML and exports | no execution of uploaded content |

## Lifecycles
Strategy: create → initialize → ordered callbacks → on_end → dispose. Terminal
exception prevents further intents and records failure. Warm-up invokes indicators
with visible history but suppresses order submission and performance accumulation.

Order: CREATED → SUBMITTED → ACCEPTED → PARTIALLY_FILLED → FILLED. SUBMITTED may
be REJECTED; ACCEPTED/PARTIALLY_FILLED may become CANCELLED, EXPIRED or receive a terminal
REJECTED event for the unfilled residual after a fill-time risk failure. That event
preserves all executed quantity and immutable fills; rejection never undoes executions.
Release only unfilled cash/quantity reservations on terminal rejection, cancellation
or expiry; never release resources already consumed by fills.
Terminal states cannot
receive new fills except explicitly reconciled out-of-order broker events with
validated execution time, represented as additional facts rather than rewritten events.
One order has many fills. A trade is reconstructed FIFO matched quantity, separate
from both order and fill. Reversals close old lots before opening new lots.

Job: QUEUED → RUNNING → SUCCEEDED | FAILED | CANCELLED. Run success requires all
artifacts committed; interrupted staging remains non-successful. Corrections create
a new run or compensating event referencing the original.

## Agent contracts
At M13 agents use authenticated role-scoped application APIs, each request including
actor_agent_id, issue_id, correlation_id and idempotency key for job submission.
Authentication establishes actor identity; a supplied actor string alone grants nothing.
Research submits jobs; validation reads artifacts and records challenges; risk reads
exposure and decision records; operations reconcile; reporting exports. Builder roles
use explicit development assignments, not unrestricted terminal execution privileges.
All roles must later produce one relevant interaction and evidence artifact; unsupported
role capabilities are rejected explicitly. No agent receives brokerage credentials.
