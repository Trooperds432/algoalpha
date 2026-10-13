# M2 historical data gate evidence

Status: M2 gate PASSED on 2026-09-25 after independent review and final checks.
Independent Data QA ALG-376 found a snapshot content-integrity blocker; it was
fixed, regression-tested, and independently re-reviewed. The separate
collaboration-agent review is documented in
`docs/validation/internal-independent-m2-review.md`; it is not represented as
a Paperclip employee run.

Implemented validated immutable instrument, OHLCV and corporate-action schemas;
XNYS daily/minute sessions with holidays, early closes and timezone conversion;
missing, duplicate and out-of-order diagnostics; strict CSV/Parquet import;
Decimal Parquet storage, content-addressed snapshots with source/input/content/
file hashes and calendar/tzdata provenance; parameterized DuckDB internal query;
clock-scoped DataFeed with publication-time filtering. Corporate-action cash
accounting and adjustment are deliberately later-milestone work.

Current local check: `docs/validation/checks-20260925T150948701010Z.json`
records Ruff lint/format, strict mypy, 66 tests and pip consistency passing.
The deterministic example `examples/m2_known_dataset.py` loaded the same two-row
synthetic XNYS dataset in two fresh stores; both runs produced canonical content
hash `4fa468966ef5db526cb1be8603ab65dc36e397df1a1604a9afca4416a6c67c07`.
At the early-close end timestamp it exposed one prior bar; one minute later it
exposed both. `coordination/check_m0.py` still passed its historical baseline.
Coordinator review then found and fixed a historical-as-of edge: a later
simulation clock could previously reveal a bar in a query whose own end was
before publication. Both DataFeed methods now enforce availability against
the requested end, with a regression assertion; the current check above is
after this fix.

Dependencies: pyarrow 25.0.1, DuckDB 1.5.5, holidays 0.105, tzdata 2026.4,
pytz 2026.4, with full environment pins in requirements-dev.lock. The first
calendar dependency, exchange_calendars, could not import pandas on this
workstation due to Windows Application Control; it was removed from final
dependencies. Local automatic watcher and GitHub workflow remain as in M1;
the hosted workflow has not run.

Limits: all data is synthetic. No real vendor point-in-time or entitlement
claim, strategy alpha, broker interaction, order, paper test or live deployment
is implied. Imports are single-instrument/single-frequency, XNYS, regular
hours, 1d/1m. No adjustment or dividend/split accounting yet. The visible
DataFeed boundary protects cooperative strategy callers; Python private
attributes are not an adversarial sandbox. First/last absent sessions outside
the imported interval are not detectable without an expected range.

ALG-376 demonstrated that rewriting a valid Parquet value and updating only
the manifest's `parquet_sha256` allowed the old snapshot ID and content hash to
reload. The failure report is preserved at paperclip-data-qa-m2.md. The same
review's Ruff-format failure overlapped a coordinator test edit; the later
checks-20260925T150654724035Z.json passed. Both findings remain part of the
audit trail. `load_snapshot` now revalidates strict schema, all rows, order,
metadata and publication timing and recomputes canonical content hash and row
count. A regression that rewrites a valid close and updates the Parquet file
checksum fails before the fix and passes after it. The current 66-test check
above follows this remediation; the separate independent re-review passed.

The final independent review passed the repaired integrity, correction identity,
historical visibility and calendar paths and ran 38 focused tests. Paperclip
re-review attempts for ALG-378 and ALG-379 failed after bounded retries with
`ACP agent reported a terminal limit failure`; those attempts remain recorded.
The coordinator used the separate read-only review in
`internal-independent-m2-review.md` for the final gate decision. M1 remains as
previously adjudicated PASS by QA ALG-375 under the roadmap's explicit local
automation allowance; the hosted GitHub workflow has not run and is not claimed.
