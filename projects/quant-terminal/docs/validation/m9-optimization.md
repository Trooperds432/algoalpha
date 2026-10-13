# M9 parameter optimisation gate

Status: **PASSED** on 2026-09-27.

## Delivered

Added immutable grid and seeded random search specifications, bounded candidate enumeration, explicit objective direction, typed M6 metric constraints, complete trial records, and diagnostic response surfaces/distributions. Grid candidates are lexicographic; random candidates use the fingerprinted SHA-256 counter/rejection sampler. Decimal candidates and fingerprints are independent of ambient decimal precision. The append-only SQLite optimization journal retains the full specification and each ordered trial with checksums, and resume validates the exact candidate prefix. Storage/provenance failures stop the campaign; explicitly classified candidate-local errors remain as failed trials. M8-backed successful trials are exercised through actual M5 backtesting, M6 metrics, M7 reports, and immutable M8 experiment registration.

Optimizer callbacks are explicit trusted adapters. Custom objectives can be supplied as typed metrics by the evaluator. In-sample rankings are diagnostics and do not qualify a strategy for production or imply out-of-sample validity. No broker or order submission is part of M9.

## Verification

The full repository gate in [`checks-20260927T142450455017Z.json`](checks-20260927T142450455017Z.json) passed: Ruff lint, Ruff format, strict mypy over 45 source files, all **179 tests**, and `pip check`. Two symlink-creation tests were skipped because Windows symlink creation is unavailable for this account; mocked link/junction guards still ran. The M9 integration test records real M5/M6/M7 trials in M8 and reads back each run and report artifact. Independent implementation and quantitative reviews passed after fixes for Decimal precision identity, candidate-index provenance, numeric surface ordering, and JSON serialization.

| Evidence | SHA-256 |
|---|---|
| `docs/m9-optimization-contract.md` | `55EEE7E310C8935078FB8D27CAD99E1BCB76CFE33A22454B026DD4588F82F858` |
| `src/quant_terminal/optimization/models.py` | `47BE098CE8A7E78FB1E6859824079106C4A12AEDC5DE71EEECA14AFFF33EE78E` |
| `src/quant_terminal/optimization/engine.py` | `E0116A339CD56EAB98F9DEE933236FDEC64BA3CD4A9BC4440A85952ACBCD8159` |
| `src/quant_terminal/optimization/analysis.py` | `00E5496BEC84C36ACA4326D2CC4F6432187B0151F7FFE466F445F198C0935E1E` |
| `src/quant_terminal/optimization/journal.py` | `B6DD4825EB635D03F30BF903256A0C10896E2AF2F247A29498B0153C985EC961` |
| `tests/unit/test_optimization.py` | `7B803EB4B4C8B60E0CB5F6A3E5968E0FFC74EF213EA7A861242311FBC9B0E2E2` |
| `tests/integration/test_experiments.py` | `D2981363472A3DD8697D13EA36663C966DA2E593DABB4DCAB0B6060F3D922873` |
| `docs/validation/checks-20260927T142450455017Z.json` | `6652E6DAF886DCA7A42691D1FFF9CD084829898621E0CB5D049DB4486844CABE` |
