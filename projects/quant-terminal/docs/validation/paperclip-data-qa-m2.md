# M2 independent Data QA review ? FAIL

Review completed 2026-09-25 (UTC), role 7.6 Data QA Agent.
Issue: ALG-376 / 143012f1-620d-48e1-a9ea-f76fe7539211.
Run: 1cbac2f1-28d6-448b-a401-241def3028ff.

Specification verdict: FAIL on snapshot content integrity. Engineering gate verdict: FAIL on fresh formatting check. This is a completed independent review, not authorisation to advance M2.

## Blocking findings

1. **High severity, high confidence ? stored content is not bound to snapshot identity on reload.** In `src/quant_terminal/data/storage.py`, `load_snapshot` excludes `parquet_sha256` from the identity payload and never recomputes canonical content from the stored rows. Reproduction: import one valid synthetic minute bar (2026-03-09 13:30?13:31 UTC, publication 13:31:01, OHLC 100/101/99/100, volume 0); rewrite close to 100.5 with the same Arrow schema; update only manifest `parquet_sha256` to the new file SHA-256. Both `load_snapshot` and `query_bars` succeed, yielding close 100.500000000000 with unchanged snapshot ID and content hash. This breaks correction versioning and reproducibility when a maintenance/correction process rewrites the data and refreshes its file checksum. Plain file corruption is correctly rejected. This is a consistency defect, not a claim of resistance to hostile filesystem writers. Remediation direction: verify decoded canonical content against the identity-bound content hash (and row count/schema), or bind the file digest into snapshot identity. Add a regression for this exact case. Coordinator/implementation owner must resolve before M2 gate.

2. **Medium severity, high confidence ? required fresh check command fails.** `.venv/Scripts/python.exe scripts/check.py` ran exactly once, exit 1. Ruff formatting rejects `tests/integration/test_data_feed.py:81`; Ruff lint, strict mypy, all 65 tests and pip consistency pass. Preserve `checks-20260925T150650071307Z.json`; do not substitute previous green evidence. Implementation owner must format the assertion and produce fresh checks on the final revision. Pytest also reports a cache-directory permission warning; tests still pass, so the warning is environmental and not the cause of failure.

## Requirement-to-evidence matrix

| Requirement/risk | Fresh evidence | Result |
| --- | --- | --- |
| Explicit CSV/Parquet schema | Independent reordered CSV columns, missing Parquet volume field; suite roundtrip | Reject malformed schema; pass |
| Duplicate/order validation before sorting | Independent duplicate and reversed minute rows | Both rejected |
| Missing observations | Independent 13:30 and 13:32 minute bars | Exactly 13:31 reported, no fill |
| OHLC/volume/nonfinite values | Independent high=99, volume=-1, close=NaN; existing negative cases | Rejected |
| Calendar/DST/early close | Existing holiday, July 3 closure, November 27 half-day, final-minute and seasonal UTC fixtures; independent March 9 minute accepted at 13:30 UTC | Pass for sampled dates |
| Timestamp/publication errors | Independent naive start, shifted minute start and publication before end | Rejected |
| Decimal exactness | Suite full decimal128 precision roundtrip; independent 13th fractional digit | Exact roundtrip; excess precision rejected |
| Reproducibility | Same CSV in two fresh stores; independent Parquet roundtrip | Identical snapshot IDs for same bytes; equal canonical hashes across formats |
| Integrity | Ordinary corruption rejected; valid-value rewrite plus refreshed file checksum accepted | FAIL as above |
| Future visibility | Executed feed tests at bar end, before/at publication and future requests; source inspection | Pass for tested cooperative API |
| Error paths | Empty input, malformed schema, bad timestamps, invalid prices, duplicate import and corrupt file | Covered by probe and suite; not exhaustive |

The tests assert fixed economic/time values and real Arrow/DuckDB roundtrips, rather than mocked import behavior. Coverage gaps include interruption/concurrent import stress, broad historical exchange-calendar validation and all possible malformed-file variants. Source hashing binds this review to the inspected files, not to a hosted CI run or a commit: the project files are untracked in the current checkout.

## Reproduction and scope

The bounded independent probe ran once through `.venv/Scripts/python.exe -` with synthetic CSV/Parquet inputs in the run-owned scratch directory. It used public DatasetStore import/load/query APIs and Arrow only to construct/corrupt local fixtures. It exited 0 and explicitly recorded the unexpected integrity acceptance below. To reproduce the integrity finding, use the one-row fixture described above, import with instrument QA / USD / ETF / XNYS / America/New_York / tick .01 / quantity step 1, source synthetic-qa; read bars.parquet with Arrow, change close, write with BAR_SCHEMA, refresh only parquet_sha256 in manifest.json, then reload/query the unchanged snapshot ID. No external dataset, chart, broker or trading operation was used.

Data source: agent-created synthetic observations, generated 2026-09-25. Sample dates: independent probe 2026-03-09; existing tests additionally exercise 2026 holiday/seasonal sessions. Publication time is explicitly 1 second after the independent minute ends. These fixtures include future synthetic calendar dates and are not observations of actual markets. Transaction costs, Sharpe, drawdown, benchmark and strategy performance are not applicable to schema/storage QA. No historical coverage, simulation elapsed time, professional approval or vendor entitlement is claimed. Leading/trailing missing observations remain limited by the documented inferred import interval. No quantitative anomaly threshold or quarantine policy was invented; invalid imports are rejected.

Only this report was authored in the project; the explicitly requested check runner emitted its normal dated evidence JSON and tool caches. No production, test, milestone-state or risk configuration was edited by this reviewer.

## Independent probe results

```json
[
  [
    "duplicate",
    "REJECT",
    "Duplicate or out-of-order bar start"
  ],
  [
    "reverse",
    "REJECT",
    "Duplicate or out-of-order bar start"
  ],
  [
    "nan",
    "REJECT",
    "Invalid bar row"
  ],
  [
    "negative-volume",
    "REJECT",
    "Invalid bar row"
  ],
  [
    "ohlc",
    "REJECT",
    "Invalid bar row"
  ],
  [
    "precision",
    "REJECT",
    "Invalid bar row"
  ],
  [
    "naive",
    "REJECT",
    "Invalid bar row"
  ],
  [
    "alignment",
    "REJECT",
    "Minute bar must span one scheduled session minute"
  ],
  [
    "publication",
    "REJECT",
    "Invalid bar row"
  ],
  [
    "column-order",
    "REJECT",
    "CSV columns/order must match BAR_SCHEMA"
  ],
  [
    "empty",
    "REJECT",
    "A dataset must contain at least one bar"
  ],
  [
    "reproducibility",
    "PASS",
    "71bcd64e5dd003344bcb6549c45d5c19b39525219fcea4e3bc7c65374a637f4a"
  ],
  [
    "missing",
    "PASS"
  ],
  [
    "parquet-schema",
    "REJECT"
  ],
  [
    "parquet-roundtrip",
    "PASS"
  ],
  [
    "file-corruption",
    "REJECT"
  ],
  [
    "content-integrity",
    "ACCEPTED CHANGED CONTENT",
    "100.500000000000",
    true,
    true
  ]
]
```

## Source SHA-256 at report time

```json
{
  "src/quant_terminal/__init__.py": "fac90e3e4543031b65eaf1b3022920e7453b55549874d953504fca4017c0af0f",
  "src/quant_terminal/config.py": "70ac764303cc8643be25bafc60b652ea43beb4c4f4a466b021e5381372450ac8",
  "src/quant_terminal/core/__init__.py": "89d45cf461972de09c433a629a8c967b49afa141946c08faa541a1b4ddec23e6",
  "src/quant_terminal/core/clock.py": "002333d02c512d125875930c28444bab379593da57258f53333722105a3bd5b3",
  "src/quant_terminal/core/enums.py": "ff555bf1355e38b0ab144a0d889757a76cc0cc9b431d64868b73761626be3a50",
  "src/quant_terminal/core/events.py": "a41fa16b325325b1058648a650d73d332667eda4ac5a9f332bbd88615babc84b",
  "src/quant_terminal/core/exceptions.py": "879dcbacef34305cc520d42986f8fa11c89e8f885043d2845a783d7943dfd711",
  "src/quant_terminal/core/identifiers.py": "443c98476bdb0ff8b5f2b38179abbd6056c37c317903f9669a1f8129cca60d84",
  "src/quant_terminal/core/serialization.py": "3e462da3f422033f0620d8c9cafa0c401790e14ed17eaebb8be3a0d7dc3e469a",
  "src/quant_terminal/data/__init__.py": "f37ecf92786ce13fe600b21d210ae9314f4485af24d2902f7408cbcec6956ce6",
  "src/quant_terminal/data/calendar.py": "6400d1922270962e0877ad5454c0e22ce45035dd6684525eaf9d360f02bdf4d3",
  "src/quant_terminal/data/feed.py": "0b1f838db447b27759bc3f388c1879b746b4aa6c8b3a493154db9512e2a9979b",
  "src/quant_terminal/data/model.py": "48e4e93874db8729b1b1366b11f85f42821eae7fcca0840a0e71adc585a207fc",
  "src/quant_terminal/data/storage.py": "387528ffc0903d2da36303cbf7bf96f9bddd54f8bf761ad9e421c7ea411ff929",
  "src/quant_terminal/logging.py": "18d1199874e705790971ef0d3bf61f4413c4bd4ee0fbf40ec4d9be6ea461fd4e",
  "tests/integration/test_data_feed.py": "8f8b2c089b174169cc3995499d9adf9fcf2b1cc6b063474a917fdf11f4a45257",
  "tests/integration/test_data_storage.py": "a6aef3dc909e242122151a965becf8051cd3fb2b9ef2835a70ef800b87a1d7fd",
  "tests/integration/test_foundation.py": "45ce6ccc1031bffc99816c06ea161b3747a1260e689b5d1ab617e43124e67163",
  "tests/unit/test_config_logging.py": "a6cddd6d62de8bad7ec5946fa347b3578a2519d67f06bcebc2d81a3be55e0df3",
  "tests/unit/test_core.py": "5c67d333b573b55e0b89d22bf7186b3fd2d816e779cd1e65f113f13601f91ba3",
  "tests/unit/test_data_model_calendar.py": "1228db5537ffbead297d0b2f4f2b26fb15147a643cbb328b2839d60445d7e2f2",
  "docs/m2-data-contract.md": "c32636618f38149dcd5d7dced61efbd5a185f2e27c1d29cd4b0c35f13b672482",
  "docs/sources/owner-brief.txt": "c18dae786b2145bbd606494a6aaf52dbd3bece61616dee3f81ca0975a6eef02e",
  "requirements-dev.lock": "9889a0673739772edec03bfaad6f3222f3f584b3efd4a0a853acbb4660718353"
}
```

Files differing from the check-run hashes at report time: ["tests\\integration\\test_data_feed.py"]. No re-run was made under the once-only instruction.

## Follow-up: historical as-of fix (2026-09-25 UTC)

Issue ALG-376; run d70a0f44-4ec2-4cd6-8d1c-0d4acfa9c5c8. Responds to board comment 8d5a330f-a726-4b30-9be2-623cc8ac38ea. This addendum preserves the original failure evidence above.

**Requested as-of change: PASS. Overall M2 gate: not approved by this review.** Both `DataFeed.between` and `history` require bar.end and available_at to be no later than the requested end, and reject requests beyond the simulation clock. The regression advances the clock to publication, confirms visibility, then queries the historical pre-publication end through both methods and expects no bars. This correctly covers the reported edge for trusted strategy callers.

Reviewed `checks-20260925T150654724035Z.json`: all five commands exit 0 (lint, formatting, mypy, 65 tests, pip). It supersedes the earlier formatting failure for its recorded revision; the failed report remains valid historical evidence. Evidence JSON SHA-256: `4637c810a8c0849c628b154b583f2b694872251e0a172b6110c878dfc57c6972`.

Fresh bounded verification: `.venv/Scripts/python.exe -m pytest tests/integration/test_data_feed.py -q -p no:cacheprovider` exited 0, **1 passed in 0.59s**. The full check runner was not repeated. The synthetic fixture and scope limitations above apply; no external data or trading was used.

Feed source and regression hashes match the cited evidence:

- `src/quant_terminal/data/feed.py`: `0b1f838db447b27759bc3f388c1879b746b4aa6c8b3a493154db9512e2a9979b`
- `tests/integration/test_data_feed.py`: `8f8b2c089b174169cc3995499d9adf9fcf2b1cc6b063474a917fdf11f4a45257`

Comparison of all 21 recorded source/test hashes found two differences in the concurrently changing workspace:

- `src/quant_terminal/data/storage.py`: now `e58e1a0473e26547545039eb3511602ba9d732cf4feb8f0182e95ebd9d9d10c3`, versus `387528ffc0903d2da36303cbf7bf96f9bddd54f8bf761ad9e421c7ea411ff929` in the cited checks.
- `tests/integration/test_data_storage.py`: now `bd037133a67b6a8623aab6d3ce7685486ee2d5c852bd41924f8d23651360fe9f`, versus `a6aef3dc909e242122151a965becf8051cd3fb2b9ef2835a70ef800b87a1d7fd` in the cited checks.

Current storage source includes canonical content verification, indicating remediation in progress. The earlier reproduced integrity defect applies to the earlier hash; this addendum does not assert it still reproduces on the changed implementation. Coordinator/implementation owner must supply fresh checks and obtain independent integrity re-review for the changed storage revision before closing that gate finding. The cited 65-test report cannot certify these subsequent changes. Hash observations are point-in-time, not a frozen commit or hosted CI guarantee.
