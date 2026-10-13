# M16 independent worker and protocol review

Date: 2026-10-01. Reviewer: `/root/m16_worker_tests` (Codex review subagent,
not a Paperclip employee run). Scope: container worker and JSON protocol, plus
authorized harmless host tests. Production files were read only by this reviewer.

## Verdict

Worker callback contract: **pass** for the focused behavior below. Overall M16
security gate: **partial**, pending actual container isolation, hostile workloads,
trusted-side admission/lifecycle, and integrated verification. Host tests cannot
establish containment of arbitrary Python.

The worker invokes `on_bar` once using the final supplied observation and copied
history. The scheduler must withhold future observations before creating the
request. Ordinary same-process Python cannot protect data already provided to it.

## Findings

- Dependency declarations were initially ignored by `execute`. The coordinator
  added a guard rejecting nonempty dependencies before source compilation. A
  harmless source that would raise if loaded verifies `dependencies_unsupported`.
  This supports the current standard-library-only environment; it does not claim
  third-party package admission or hash verification.
- Protocol `_decode` initially allowed the JSON integer digit-limit `ValueError`
  to escape its structured `ProtocolError` boundary. Fresh reproduction:
  `decode_response(b'{"x":' + b'1' * 5000 + b'}')` produced `ValueError`.
  The coordinator corrected decoding and added
  `test_excessive_json_integer_has_structured_protocol_failure`. Independent
  fresh retest confirmed the malformed message now raises `ProtocolError`.
  This finding is resolved at protocol hash
  `caa9342eb746f6eb38ff12bc7d4ad751310a415a573228195d1037c4c7c86394`.

## Requirement to evidence

| Requirement | Direct evidence | Result |
| --- | --- | --- |
| One callback on final as-of row | Two-row fixture asserts one invocation, index 1, seed 42, exact history and final row | Pass |
| Input copies | Callback mutates history and bar; original request remains unchanged and bar is independent of history | Pass |
| Empty observations | Callback that would fail is not invoked; empty intents returned | Pass |
| Typed economic proposals | Limit order round trip; zero, negative, nonfinite and boolean quantity rejection | Pass |
| Stable sanitized failures | Syntax, missing callback, ordinary exceptions, SystemExit, malformed intent types/counts; private marker absent | Pass |
| Output byte cap | Captured harmless `_emit` writes stay within budget; too-small budget emits no bytes | Pass |
| Unsupported dependencies | Nonempty declarations rejected before harmless source load | Pass |
| JSON parser failure normalization | Regression test verifies oversized integer raises ProtocolError | Resolved; pass |
| Network/files/processes/resources | No hostile execution on host | Not established here |

## Fresh checks

- Initial `.venv/Scripts/python.exe -m pytest tests/unit/test_sandbox_worker.py tests/unit/test_sandbox_protocol.py -q`: 26 passed in 0.18s; decoder defect separately reproduced and preserved above.
- Final `.venv/Scripts/python.exe -m pytest tests/unit/test_sandbox_worker.py tests/unit/test_sandbox_protocol.py tests/integration/test_m16_strategy_bridge.py -q`: 33 passed in 1.53s.
- After provenance integration, independent `.venv/Scripts/python.exe -m pytest tests/unit/test_sandbox_worker.py tests/unit/test_sandbox_protocol.py tests/integration/test_m16_strategy_bridge.py tests/integration/test_event_backtest.py -q`: 50 passed in 1.30s.
- `.venv/Scripts/python.exe -m ruff check tests/unit/test_sandbox_worker.py`: passed.
- `.venv/Scripts/python.exe -m ruff format --check tests/unit/test_sandbox_worker.py`: passed.

Worker SHA-256 reviewed:
`0848ea3a7c9a9a16042203b677004aecd63219628c1ae1526395c409d0fb8bf9`.
Protocol SHA-256 at reproduction:
`b01f1295a88274ac5ddec2cbe70deb25c6c8c2f633169a5941ef89a6fb3cc255`.
Any production revision requires an appropriate fresh retest. No full project
suite was run during concurrent backend edits. No image build, broker connection,
orders, external messages, or hostile host source execution was performed.

## Trusted strategy adapter review

Read-only review of `sandbox/strategy.py`, the integration fixtures, ordinary
`StrategyContext`, `DataFeed`, and `BacktestEngine` found no authority or risk
bypass in the adapter. Source receives scalar allowlisted bar values rather than
feed, portfolio, ledger or broker objects. Only currently visible history is
serialized; `Bar` validation guarantees `available_at >= end`. Responses are
revalidated with matching job identity and success status. Trusted metadata
constructs proposals, other-instrument proposals fail, and market/stop buy caps
are supplied before the existing cash/notional/inventory execution checks.
Fresh bridge tests verify as-of slices, preserved golden economics, notional
rejection and invalid economic/unconfigured proposals.

The prior provenance finding is **resolved** by the trusted optional
`StrategyRunMetadata` hook. `begin_run` clears accumulated slices/identities, and
the engine copies `run_metadata` into the result. Isolated results now record
source hash, ordered aggregate data-slice hash and count, empty dependency-set
hash, budget, seed, protocol version and backend identity. The backend identity
must remain stable across calls; a change raises `DomainError`.

Independent harmless test-double execution verified exact source/dependency/data
hashes against separately computed SHA-256 values, exact decoded budget, two
repeat runs with equal metadata and slice count 2 rather than 4, and explicit
`not_executed` identity/count 0 before any observation and after reset. No strategy
source was executed for these checks. Public admission is still disabled pending
runtime verification, which this adapter review cannot establish.

Coverage gaps for coordinator consideration: explicit wrong-job/error responses,
delayed-publication fixtures, cumulative cash reservations, short-sale rejection,
and cross-instrument proposals. Existing engine coverage supports general risk
semantics, but the focused adapter fixtures do not directly exercise all these
paths. This review does not certify container launch, lifecycle or isolation.
