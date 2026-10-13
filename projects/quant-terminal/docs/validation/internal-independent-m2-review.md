# Independent M2 data review

Review date: 2026-09-25 (UTC). Review run: separate read-only collaboration-agent review; focused command `\.venv\Scripts\python.exe -m pytest tests/integration/test_data_storage.py tests/integration/test_data_feed.py tests/unit/test_data_model_calendar.py -q` completed with **38 passed** in 1.37 seconds. This review does not change milestone state.

Paperclip re-review attempts could not run: its agent runs failed with `ACP agent reported a terminal limit failure`. This artifact records the resulting separate collaboration-agent review; it is not represented as a Paperclip run.

## Files reviewed and evidence identity

The following SHA-256 hashes were captured before writing this report:

| File | SHA-256 |
| --- | --- |
| `src/quant_terminal/data/storage.py` | `E58E1A0473E26547545039EB3511602BA9D732CF4FEB8F0182E95EBD9D9D10C3` |
| `src/quant_terminal/data/feed.py` | `0B1F838DB447B27759BC3F388C1879B746B4AA6C8B3A493154DB9512E2A9979B` |
| `src/quant_terminal/data/model.py` | `48E4E93874DB8729B1B1366B11F85F42821EAE7FCCA0840A0E71ADC585A207FC` |
| `src/quant_terminal/data/calendar.py` | `6400D1922270962E0877AD5454C0E22CE45035DD6684525EAF9D360F02BDF4D3` |
| `tests/integration/test_data_storage.py` | `BD037133A67B6A8623AAB6D3CE7685486EE2D5C852BD41924F8D23651360FE9F` |
| `tests/integration/test_data_feed.py` | `8F8B2C089B174169CC3995499D9ADF9FCF2B1CC6B063474A917FDF11F4A45257` |
| `tests/unit/test_data_model_calendar.py` | `1228DB5537FFBEAD297D0B2F4F2B26FB15147A643CBB328B2839D60445D7E2F2` |

Also inspected `AGENTS.md`, `docs/roadmap.md`, `coordination/milestone-state.json`, `docs/m2-data-contract.md`, and `docs/validation/m2-historical-data.md`.

## Findings

**ALG-376 snapshot-integrity blocker: closed in the reviewed code.** `load_snapshot` validates the manifest identity, Parquet file hash, schema, rows, row count, metadata, publication timing, calendar validity, row ordering and recomputed canonical content hash. Rewriting a valid Parquet value and refreshing only `parquet_sha256` therefore fails under the old snapshot ID at the canonical-content check. The regression test `test_rewritten_parquet_with_refreshed_file_hash_cannot_keep_identity` covers this. Corrected input imported normally changes the content and/or input hash, which contributes to a new snapshot ID; the tests also establish that equal CSV/Parquet content with different input hashes gets distinct IDs. No blocker was found in the requested paired-rewrite or correction-identity paths.

**Historical as-of visibility: passes review.** `DataFeed.between` and `history` require both `bar.end <= requested end` and `bar.available_at <= requested end`, while requests beyond simulation time raise `FutureDataError`. The feed regression covers querying before publication even after the simulation clock has advanced.

**Calendar/data checks: no M2 blocker found.** The reviewed implementation validates XNYS regular sessions, holidays, early closes, daylight-saving offsets, one-minute alignment, OHLC constraints and nonnegative volume; the focused 38-test run passed.

## Residual documented scope limits

All reviewed examples are synthetic; no real vendor publication-history or entitlement behavior is verified. Corporate-action adjustment and cash accounting are deferred. Leading/trailing missing sessions require a caller-declared expected range. `DataFeed` is an application boundary for trusted callers, not an adversarial Python sandbox. These limitations are documented in the M2 contract/report and do not block the reviewed M2 contract.

This review is a PASS for the requested M2 integrity, as-of and calendar paths, with no blocker found. It does not itself mark M2 passed. Separate existing evidence discrepancy for the coordinator: `docs/roadmap.md` requires hosted CI for M1, while `docs/validation/m2-historical-data.md` says the hosted workflow has not run and M1 is recorded as passed in `coordination/milestone-state.json`. That discrepancy is outside this M2 code review and should be resolved before relying on the M1 gate evidence.
