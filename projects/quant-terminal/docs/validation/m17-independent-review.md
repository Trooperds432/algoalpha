# M17 independent review

Status: PASS for the owner's M17 local paper trading gate. No unresolved material
finding remains in the reviewed M17 surface. Specification and engineering-quality
axes both pass within the stated local synthetic scope. This does not pass M16.

Reviewer: actual collaboration task `/root/m17_validation`, assigned the integration
test file and this review only. No broker, external feed, credentials or authorised
strategy forward test was used. All observations are synthetic and cash is virtual.

The independent fixture starts with USD 10,000 and a ten-unit buy. Orders cannot fill
on their proposal bar. Volume eight at 50% participation gives four-unit fills.
At prices 101, 102 and 103, a USD 0.10/unit commission with USD 1 minimum produces
cash 9,595, 9,187 and 8,981; marked NAV is 9,999, 10,003 and 10,011. The minimum
is charged once across partial fills. These expected amounts were hand calculated,
not obtained from implementation output.

Coverage in `tests/integration/test_paper_session.py`: owner disconnect, reconnect,
duplicate fills, missing updates, out-of-order sequences/bars, stale observations and
process restart; future data; pending and partially filled order recovery; mismatching
reconciliation; corrupt payload, projection, event identity and request hash; concurrent
duplicate writers; injected SQLite write abort and retry; strategy-free replay.

Findings communicated to the coordinator:

- Unknown/conflicting fill acknowledgements originally rejected without halting
  the connected session. Coordinator added a durable fault. Regression passed.
- Integrity digests originally omitted event IDs and duplicate request hashes.
  Coordinator added both to the digest. Four corruption regressions passed.
- Changed duplicate observations originally rejected without halting subsequent
  processing. Coordinator added a durable halt; regression passed.
- Deterministic fault IDs deduplicated repeat faults after reconciliation, allowing
  another reconciled operator to continue processing. The two-object regression
  observed `connected=True` after a second identical bad fill. Coordinator made fault
  IDs unique; regression passed, including the second operator being blocked.
- Coordinator added a transactionally committed journal head. Independent deletion
  tests prove removing the committed tail or head fails reconstruction. Concurrent
  distinct event IDs with the same sequence produce only one fill batch and a durable
  halt for the sequence conflict.

Preserved experiments: first run 15 passed / 1 failed due to an overly specific
error-message regex in the test (corrected to observe domain failure); subsequent run
16 passed. Expanded run 17 passed / 1 failed on the changed duplicate halt defect.
Expanded repeat-fault runs were 18 passed / 1 failed and then 21 passed / 1 failed;
the failing case is retained. Final focused run: 22 passed in 1.34 seconds, command
`.venv/Scripts/python.exe -m pytest tests/integration/test_paper_session.py -q`.
Test file has 22 cases and passes Ruff and formatting checks.

Independent integrated retest on the final stable source:
`.venv/Scripts/python.exe -m pytest tests/unit/test_paper_feed.py
tests/integration/test_paper_session.py tests/integration/test_m17_interface.py -q`
completed with 42 passed in 2.07 seconds. The API fixture independently asserts cash
8,999 and NAV 10,009 after ten units fill at 100, commission 1, and closing mark 101.
The CLI creates only a disconnected session and a real CLI subprocess reconstructs
the same status as the API. Source and unsupported configuration rejection precede
state creation. Review of the shared service and command wiring found no additional
material issue.

Reviewed fresh full-check artifact:
`docs/validation/checks-20261001T173906465423Z.json`: Ruff check, Ruff format, mypy,
pytest and pip check all exited zero. Pytest: 352 passed, 32 skipped, one existing
Starlette/httpx deprecation warning. The skipped cases are 30 explicitly opt-in M16
hostile container cases and two Windows symlink privilege cases; no M17 case was
skipped. M16 containment is not established by this run. Current SHA-256 values of
the seven M17/public-interface source files and three M17 test files were separately
compared with the artifact and all ten matched.

Review boundaries: journal digests detect corruption and truncation against the
committed head; they are not authenticated protection against an attacker able to
rewrite the database and every digest. Matching remains the documented OHLC and
participation simulator, with no claim of exchange queue realism. Injected SQL abort
proves a failed write cannot commit economics; restart fixtures independently prove
durable reconstruction and mandatory reconciliation. No external price stream or
broker test is claimed.

The owner restart/accounting gate is satisfied by the exact economic fixture,
partially filled reconstruction, strategy-free replay, explicit reconciliation,
durable disconnect/faults, duplicate protection, concurrent writers, rollback and
corruption detection. Browser automation exposed no available tab, so live visual
UI interaction remains unverified and is not counted as acceptance evidence.
UI compilation and existing UI tests were coordinator-reported; this verdict rests
on direct API/CLI and domain evidence. Feed scope is one instrument, one currency,
trusted built-in strategies and at most 10,000 commands per session.
