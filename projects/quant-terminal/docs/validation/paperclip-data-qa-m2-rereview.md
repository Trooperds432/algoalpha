# ALG-378 independent M2 integrity re-review

Reviewed 2026-10-03 UTC by role 7.6 Data QA Agent (192c5bef-fcdc-46eb-bfe5-b149022e9c47).
Issue: ALG-378 / 4e6500e0-ab7e-43aa-93cc-4e500ad50766.
Run: 0323642a-8069-4f20-a296-f46de2b2496f.

**M2 integrity remediation: PASS. Current full engineering check: FAIL.** This completes the assigned re-review with exact remaining check failures; it does not approve the current project gate or alter milestone state. The workspace is now at M22. The prior interrupted runs are not counted as successful reviews.

## Integrity evidence

Read the original paperclip-data-qa-m2.md failure and m2-historical-data.md gate record. Inspected load_snapshot: the manifest payload binds content_hash and row_count to snapshot_id; only snapshot_id and parquet_sha256 are excluded. Loading checks the file checksum, exact Arrow field schema (schema metadata intentionally ignored), decodes every row through Bar validation, checks count, instrument/source, publication delay, calendar validity and ordering, and recomputes canonical content before returning. query_bars reloads through the same validation. This addresses the paired rewrite defect for identity-bound imported snapshots; it is not an adversarial filesystem or concurrent-writer security guarantee.

Independently generated the original one-minute synthetic fixture in run-owned scratch: QA/USD/etf/XNYS/America_New_York (instrument timezone America/New_York), tick .01, quantity step 1; 2026-03-09 13:30?13:31 UTC, publication 13:31:01 UTC; OHLC 100/101/99/100, volume 0, source synthetic-qa. Original import and query succeeded with exact Decimal close 100. Rewrote close to 100.5 with BAR_SCHEMA and changed only manifest parquet_sha256. Both load and query rejected under the original snapshot ID. Additional schema/count/invalid-row probes refreshed only the file checksum and also rejected.

Snapshot ID: `71bcd64e5dd003344b6549c45d5c19b39525219fcea4e3bc7c65374a637f4a`.
Original canonical content hash: `4ae2b54848d264c2e2b63913c03ce0ce1e71e0fb3d868982ee743ebd0a09cda1`.

```json
{
  "load": "Snapshot canonical content hash mismatch",
  "query": "Snapshot canonical content hash mismatch",
  "schema": "Snapshot Parquet schema mismatch",
  "row_count": "Snapshot row count mismatch",
  "invalid_row": "Invalid bar row"
}
```

The existing regression test_rewritten_parquet_with_refreshed_file_hash_cannot_keep_identity rewrites a valid close from 100.25 to 100.26, refreshes only the file checksum, and expects DomainError matching content hash. It was included in the fresh suite; neither storage nor feed tests failed. No tests or production code were edited.

## Fresh checks and remaining blockers

Executed `.venv/Scripts/python.exe scripts/check.py` exactly once in this run, exit 1. Retained its normal generated evidence: `docs/validation/checks-20261003T115821949024Z.json` (SHA-256 b317b0353ab45800176a5ef13d4a3489adf6ad83425b7e9b38d95489a1a6493b).

- Ruff lint: PASS.
- Ruff format --check src tests: PASS, 148 files already formatted.
- Strict mypy: PASS, 97 source files.
- Pytest: 640 passed, 2 failed, 32 skipped, 3 warnings, 63.51 seconds.
- Pip consistency: PASS.

Remaining blockers to claiming a current full-check PASS:

1. tests/integration/test_m21_lifecycle.py::test_same_builtin_strategy_completes_research_and_paper_lifecycle fails in application/runtime.py:336: IntegrityError, Executing source or dependency lock differs from prepared configuration.
2. tests/integration/test_robustness.py::test_real_scenarios_retain_costs_dates_regimes_universe_and_failures fails in robustness/adapter.py:168: IntegrityError, Executing project source changed since the baseline.

Coordinator/owners of those later-milestone surfaces must establish a stable source revision, investigate the source-bundle mismatches and obtain fresh passing evidence. Concurrent edits are a possible cause, not established by these errors alone. This reviewer did not repair unrelated code or repeat the once-only runner. Thirty skips require the explicit hostile-container opt-in, and two require Windows symlink capability. These skipped checks are not passes. Warnings concern httpx deprecation and two pytest-cache permission failures; they are not the asserted test failures.

## Hash comparison and reproducibility

Compared all 21 source/test entries in checks-20260925T150948701010Z.json (historical 66-test PASS). Twenty match exactly, including storage.py, feed.py and their regression tests. Only src/quant_terminal/__init__.py differs. Later source/test additions mean the historical evidence does not certify the whole current tree. Full comparison follows. Fresh evidence supplies all current source/test hashes. Differences against that fresh evidence at report generation: [].

```json
[
  {
    "path": "src\\quant_terminal\\__init__.py",
    "historical": "fac90e3e4543031b65eaf1b3022920e7453b55549874d953504fca4017c0af0f",
    "current": "b9e9c849fc505be4eeaafb335cf42578ee32109925cc6968a1814221f6d2e321"
  },
  {
    "path": "src\\quant_terminal\\config.py",
    "historical": "70ac764303cc8643be25bafc60b652ea43beb4c4f4a466b021e5381372450ac8",
    "current": "70ac764303cc8643be25bafc60b652ea43beb4c4f4a466b021e5381372450ac8"
  },
  {
    "path": "src\\quant_terminal\\core\\__init__.py",
    "historical": "89d45cf461972de09c433a629a8c967b49afa141946c08faa541a1b4ddec23e6",
    "current": "89d45cf461972de09c433a629a8c967b49afa141946c08faa541a1b4ddec23e6"
  },
  {
    "path": "src\\quant_terminal\\core\\clock.py",
    "historical": "002333d02c512d125875930c28444bab379593da57258f53333722105a3bd5b3",
    "current": "002333d02c512d125875930c28444bab379593da57258f53333722105a3bd5b3"
  },
  {
    "path": "src\\quant_terminal\\core\\enums.py",
    "historical": "ff555bf1355e38b0ab144a0d889757a76cc0cc9b431d64868b73761626be3a50",
    "current": "ff555bf1355e38b0ab144a0d889757a76cc0cc9b431d64868b73761626be3a50"
  },
  {
    "path": "src\\quant_terminal\\core\\events.py",
    "historical": "a41fa16b325325b1058648a650d73d332667eda4ac5a9f332bbd88615babc84b",
    "current": "a41fa16b325325b1058648a650d73d332667eda4ac5a9f332bbd88615babc84b"
  },
  {
    "path": "src\\quant_terminal\\core\\exceptions.py",
    "historical": "879dcbacef34305cc520d42986f8fa11c89e8f885043d2845a783d7943dfd711",
    "current": "879dcbacef34305cc520d42986f8fa11c89e8f885043d2845a783d7943dfd711"
  },
  {
    "path": "src\\quant_terminal\\core\\identifiers.py",
    "historical": "443c98476bdb0ff8b5f2b38179abbd6056c37c317903f9669a1f8129cca60d84",
    "current": "443c98476bdb0ff8b5f2b38179abbd6056c37c317903f9669a1f8129cca60d84"
  },
  {
    "path": "src\\quant_terminal\\core\\serialization.py",
    "historical": "3e462da3f422033f0620d8c9cafa0c401790e14ed17eaebb8be3a0d7dc3e469a",
    "current": "3e462da3f422033f0620d8c9cafa0c401790e14ed17eaebb8be3a0d7dc3e469a"
  },
  {
    "path": "src\\quant_terminal\\data\\__init__.py",
    "historical": "f37ecf92786ce13fe600b21d210ae9314f4485af24d2902f7408cbcec6956ce6",
    "current": "f37ecf92786ce13fe600b21d210ae9314f4485af24d2902f7408cbcec6956ce6"
  },
  {
    "path": "src\\quant_terminal\\data\\calendar.py",
    "historical": "6400d1922270962e0877ad5454c0e22ce45035dd6684525eaf9d360f02bdf4d3",
    "current": "6400d1922270962e0877ad5454c0e22ce45035dd6684525eaf9d360f02bdf4d3"
  },
  {
    "path": "src\\quant_terminal\\data\\feed.py",
    "historical": "0b1f838db447b27759bc3f388c1879b746b4aa6c8b3a493154db9512e2a9979b",
    "current": "0b1f838db447b27759bc3f388c1879b746b4aa6c8b3a493154db9512e2a9979b"
  },
  {
    "path": "src\\quant_terminal\\data\\model.py",
    "historical": "48e4e93874db8729b1b1366b11f85f42821eae7fcca0840a0e71adc585a207fc",
    "current": "48e4e93874db8729b1b1366b11f85f42821eae7fcca0840a0e71adc585a207fc"
  },
  {
    "path": "src\\quant_terminal\\data\\storage.py",
    "historical": "e58e1a0473e26547545039eb3511602ba9d732cf4feb8f0182e95ebd9d9d10c3",
    "current": "e58e1a0473e26547545039eb3511602ba9d732cf4feb8f0182e95ebd9d9d10c3"
  },
  {
    "path": "src\\quant_terminal\\logging.py",
    "historical": "18d1199874e705790971ef0d3bf61f4413c4bd4ee0fbf40ec4d9be6ea461fd4e",
    "current": "18d1199874e705790971ef0d3bf61f4413c4bd4ee0fbf40ec4d9be6ea461fd4e"
  },
  {
    "path": "tests\\integration\\test_data_feed.py",
    "historical": "8f8b2c089b174169cc3995499d9adf9fcf2b1cc6b063474a917fdf11f4a45257",
    "current": "8f8b2c089b174169cc3995499d9adf9fcf2b1cc6b063474a917fdf11f4a45257"
  },
  {
    "path": "tests\\integration\\test_data_storage.py",
    "historical": "bd037133a67b6a8623aab6d3ce7685486ee2d5c852bd41924f8d23651360fe9f",
    "current": "bd037133a67b6a8623aab6d3ce7685486ee2d5c852bd41924f8d23651360fe9f"
  },
  {
    "path": "tests\\integration\\test_foundation.py",
    "historical": "45ce6ccc1031bffc99816c06ea161b3747a1260e689b5d1ab617e43124e67163",
    "current": "45ce6ccc1031bffc99816c06ea161b3747a1260e689b5d1ab617e43124e67163"
  },
  {
    "path": "tests\\unit\\test_config_logging.py",
    "historical": "a6cddd6d62de8bad7ec5946fa347b3578a2519d67f06bcebc2d81a3be55e0df3",
    "current": "a6cddd6d62de8bad7ec5946fa347b3578a2519d67f06bcebc2d81a3be55e0df3"
  },
  {
    "path": "tests\\unit\\test_core.py",
    "historical": "5c67d333b573b55e0b89d22bf7186b3fd2d816e779cd1e65f113f13601f91ba3",
    "current": "5c67d333b573b55e0b89d22bf7186b3fd2d816e779cd1e65f113f13601f91ba3"
  },
  {
    "path": "tests\\unit\\test_data_model_calendar.py",
    "historical": "1228db5537ffbead297d0b2f4f2b26fb15147a643cbb328b2839d60445d7e2f2",
    "current": "1228db5537ffbead297d0b2f4f2b26fb15147a643cbb328b2839d60445d7e2f2"
  }
]
```

Sources are agent-created synthetic fixtures generated 2026-10-03, not vendor market observations. Sample and publication dates above are fixture values; the existing regression uses synthetic November 2026 daily bars. No external data, entitlement, historical-market coverage, trading, strategy performance or elapsed paper simulation is claimed. Transaction costs, benchmark, Sharpe and drawdown are not applicable to this storage-integrity review. No quarantine threshold was invented. Only this report was authored in the project; the explicitly requested runner emitted its standard dated evidence and tool caches, and probes used run-owned scratch. Original failed evidence is preserved.


## Continuation verification and disposition ? 2026-10-04 UTC

Issue ALG-378; completion run `27e4c51a-95ba-4704-9ec8-5fd6a6c07064`. Preserved the completed independent probe and once-only full check from run `0323642a-8069-4f20-a296-f46de2b2496f`; neither was repeated. Re-read the original finding, M2 gate record, final storage implementation and paired-mutation regression.

All 147 file hashes recorded in `checks-20261003T115821949024Z.json` still match at this continuation check. All 21 historical M2 entries were compared again: 20 match, with only the package `__init__.py` differing as listed above. The historical evidence file SHA-256 is `d5dabac4992dafa80ad73132cd8f2f40d513708f2689452b50af4a10e13b80ee`; the October 3 evidence hash still matches the value above. `tests/test_m22_dispatch.py` is a later addition outside that recorded file set, so unchanged recorded hashes do not certify the full current workspace.

Fresh `.venv/Scripts/python.exe -m ruff format --check src tests` exited 0: **149 files already formatted**. The preserved full-check failure remains a dated result, not a claim that the two later-milestone tests were rerun today. The M2 integrity finding is resolved for the unchanged reviewed source. This review does not pass any current project milestone gate.

Transcription clarification: the snapshot ID printed in the original section above is short by two characters. The original ALG-376 reproducibility record gives `71bcd64e5dd003344bcb6549c45d5c19b39525219fcea4e3bc7c65374a637f4a`. This is a correction based on the preserved original report, not a new probe result.

Disposition: **review complete; M2 integrity PASS; preserved full-check FAIL with the two exact failures above**. Close ALG-378 as done because the assigned deliverable explicitly permits reporting remaining blockers. Repair and current integrated gate verification belong to the coordinator and later-milestone owners. Only this report was edited in this continuation; no production, tests, state, risk settings or trading actions were changed.
