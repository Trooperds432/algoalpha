# ALG-374 independent M1 foundation review

Verdict: BLOCKING FINDINGS — M1 gate is not passed. Review assignment complete.

- Review date: 2026-09-23 (UTC).
- Issue: ALG-374 / ac4d6d90-8520-4902-98d8-1a6d32603aa9.
- Reviewer: Quality Assurance / Automated Testing Engineer, role 27.4,
  agent 083f0ec4-4a80-4d95-b57e-7ed5825e3dd0.
- Actual review run: 1258c6aa-f301-4e5f-988c-6062cd58b444.
- Checkout was already claimed by the harness; no second checkout or delegation.
- Workspace: C:/Users/Jonat/algoalpha/projects/quant-terminal.
- Revision: UNCOMMITTED; `git rev-parse HEAD` failed because no initial commit existed.
  Shared workspace includes concurrent ALG-373 work; this verdict applies only to
  the reviewed hashes, not subsequent changes. No other agents' work is credited.

## Evidence and scope

Read AGENTS.md, docs/roadmap.md, coordination/milestone-state.json,
docs/validation/m1-foundation.md, the M1 owner brief, and M0 domain, data, testing,
security, architecture and backtesting contracts. Independently inspected all
src/quant_terminal/core modules, config.py, logging.py, all three test modules,
pyproject.toml, requirements-dev.lock, scripts/check.py, scripts/watch_ci.py and
.github/workflows/ci.yml. Active milestone was 1; M0 was recorded passed.

The only authored project file is this report. The explicitly required check
command automatically created checks-20260923T150453894591Z.json; that generated
check evidence is retained unchanged, including its warning. No production,
configuration, milestone state, existing test or golden output was edited.

## Blocking findings and exact follow-up

1. **Automatic CI gate is unverified.** The owner M1 gate says CI must run
   automatically and pass. The reviewed foundation report explicitly says the
   GitHub workflow was not published or run remotely. This review executed the
   pipeline manually. A watcher script and workflow definition establish capability,
   not an actual automatic run. `git config --get core.hooksPath` returned no value;
   this alone does not exclude default hooks, and no automatic hook evidence was
   available in the reviewed report. Coordinator/ALG-373 must supply dated evidence
   for an actual automatic run on the final source, clearly distinguishing local
   automation from hosted CI. No hosted success or publication is claimed here.

2. **M0 event field contract is unresolved.** docs/domain_model.md requires
   immutable records to carry `available_at`. Event and its strict JSON parser
   expose `observed_at` instead and have no `available_at`. M0 backtesting conventions
   do explicitly require `observed_at` for fills and callbacks, so this is a
   conflicting contract, not proof that economic timing is wrong. Before the gate,
   the coordinator must reconcile the generic event contract with that causal
   convention and document the chosen schema and compatibility semantics, with a
   test for the agreed fields. Do not silently rename the field or treat availability
   and economic time as interchangeable.

3. **Clock restore violates the documented typed-failure contract.**
   `SimulationClock.restore('malformed')` raises built-in `ValueError`, while
   domain_model.md says validation errors are typed failures and malformed event
   serialization already normalizes failures to `DomainError`. A caller catching
   `DomainError` misses malformed clock snapshots. Coordinator must normalize invalid
   snapshot input to a documented domain exception and add malformed/non-string/
   naive-timestamp restoration tests. Existing valid snapshot replay passes.

The coordinator owns remediation and fresh verification. This completed review
must not itself advance the milestone. No follow-up was delegated by this reviewer.

## Commands and observed results

All project commands ran from the project root.

| Command | Result |
|---|---|
| `.venv/Scripts/python.exe scripts/check.py` | Exit 0; all five stages passed |
| Python `-m ruff check src tests` (pipeline) | All checks passed |
| Python `-m ruff format --check src tests` (pipeline) | 13 files already formatted |
| Python `-m mypy src` (pipeline) | No issues in 10 source files |
| Python `-m pytest -q` (pipeline) | 24 passed, 1 warning, 0.32 seconds |
| Python `-m pip check` (pipeline) | No broken requirements |
| `.venv/Scripts/python.exe -B -` with independent assertions over stdin | Exit 0; 79 invalid-input rejection probes passed, plus positive invariants below |
| `.venv/Scripts/python.exe -I -B -c "import quant_terminal, importlib.metadata as m; from quant_terminal.core.events import Event; print(quant_terminal.__file__); print(m.version('algoalpha-quant-terminal'))"` | Isolated installed import succeeds; version 0.0.1; resolves to src via editable installation |

The pipeline ran Python 3.13.15 on Windows. Pytest reported inability to create
`.pytest_cache/v/cache/nodeids` (WinError 5). Tests still ran and passed; the warning
is preserved, not suppressed. No failed experiments were deleted.

Full pipeline commands, stdout/stderr, exit statuses and 13 source/test SHA-256
hashes are in `docs/validation/checks-20260923T150453894591Z.json`. A fresh comparison
after independent probes found **zero source/test hash changes** since that run.
The pipeline hashes after execution, so this comparison does not prove that no
transient concurrent mutation occurred during execution.

Additional reviewed SHA-256 values:

```text
pyproject.toml df5797416864db5db1bd53d5ffb095afa99e293b7b6e0a998e3911286c44b5a0
requirements-dev.lock 90d4d729808385e342b80e0a18560ea07edf98b4b157fd861f52bc2093750157
scripts/check.py ccae6f08af1c90dba5835d801d3aea369f9dbb8a0b664e990cec4861295a5d44
scripts/watch_ci.py 2a644a327fddfcfc3c279942470c8be5113443c23ebc3a1fa8d7196f9ddf4913
.github/workflows/ci.yml 2b2282db8f10e213905cc70c56a1178601361a32b5d06fad974418820b54dadc
docs/domain_model.md 97a1813698568eddb1a465be13a02486f24bc6691e9473132622c3b758f5abee
docs/roadmap.md acea3eda9c950eabc5546dabf84aef3121041c615d9166b9b01503d7d49e840b
```

## Independent probe coverage

The stdin probe used UTC 2026-01-01 and synthetic identifiers only. Its rejection
helper called the constructor/parser and required DomainError (ConfigurationError
for configuration and FrozenInstanceError for mutation); accepting a case would
raise AssertionError. The 79 successful rejection assertions were:

- 40 identifier cases: each of five types rejected None, integer 1, True, empty
  string, 129 characters, whitespace, traversal-like text and non-ASCII text.
- Five identifier value mutations failed; all instances lacked `__dict__`.
- Four utc inputs rejected: None, integer, date string and naive datetime.
- Event mutation and an OrderId supplied as run_id both failed.
- Seven event cases rejected: boolean/negative sequence, boolean/unsupported schema,
  missing event_time, string observed_at and observation before economic time.
- Six serialized field cases rejected: null/bad/naive event_time, boolean observed_at,
  numeric run_id and fractional sequence. Four malformed envelopes rejected:
  null, array, empty object and invalid JSON.
- Ten config cases rejected: relative path, UNC path, seed 2**64, boolean/zero
  workers, boolean/3601 timeout, signaling NaN/infinite capital and live_enabled=None.
- One rewind rejected after advances of 0, 1, 1 and 5 seconds.

Positive assertions verified five distinct typed identifiers with equal textual
values remain distinct in a set; exact Decimal as_tuple round trips for 1.2300,
-0.00, 1E-1000 and 123456789123456789.123456789; equal-time clock advances and snapshot
replay; nested list/dictionary redaction of API-Key, Set-Cookie and client_secret.
All secret-like values were artificial markers, never credentials.

Minimal reproduction of the two contract findings:

```python
from dataclasses import fields
from quant_terminal.core.events import Event
from quant_terminal.core.clock import SimulationClock
from quant_terminal.core.exceptions import DomainError
print([field.name for field in fields(Event)])
# event_id, run_id, sequence, event_time, observed_at, schema_version
try:
    SimulationClock.restore('malformed')
except DomainError:
    print('typed domain failure')
except ValueError:
    print('untyped ValueError observed')
```

## Confirmed behavior and limits

Identifiers and Event are frozen, slotted dataclasses with runtime checks; cross-type
identifier equality is distinct. This is normal application immutability, not an
adversarial sandbox. Core imports are standard-library/internal only.

UTC normalization, causal event validation and canonical event round trips pass.
Persistence evidence is a three-event temporary JSONL file read back and replayed
through the clock. It does not establish durable repository crash recovery, atomic
commit, duplicate sequence detection or mixed-run stream validation. Those broader
storage/engine features are not implemented M1 capabilities.

Logging removes named sensitive values recursively and avoids unknown-object repr.
The independent synthetic free-text probe confirms that arbitrary string values
remain visible, as the implementation report discloses. Do not send credentials
through free text or event names. This is field redaction, not a general secret
scanner; no claim of comprehensive credential detection is made.

Packaging is only verified as an existing editable installation plus dependency
consistency. Pytest explicitly inserts src into its import path, so pytest alone
cannot validate packaging. No clean wheel/sdist build, fresh offline installation,
Python 3.12 run or Linux workflow execution was performed. The development lock
pins versions but not hashes; build metadata requests setuptools>=77. Reproducible
release packaging and dependency provenance/license audit are not established by
this foundation check.

The Hypothesis tests exercise one generated clock offset and Decimal values; they
do not establish broad event-stream state-machine/fault coverage. Financial engine,
broker rejects/disconnects and financial metrics are outside this implemented scope.

No market data, external research, trading service or paid service was used.
Publication/sample dates and transaction costs are not applicable to these synthetic
software checks. No strategy results, Sharpe, drawdown, historical coverage or elapsed
paper simulation are claimed. No milestone advanced and no all-370 participation
claim is supported by this single review.
