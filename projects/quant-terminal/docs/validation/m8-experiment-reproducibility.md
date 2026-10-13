# M8 experiment management and reproducibility gate

Status: **PASSED** on 2026-09-27.

## Delivered

Added immutable typed experiment configuration, manifests, replay attestations and exact economic-result fingerprints. The local SQLite registry stores searchable run metadata and append-only tags beside immutable hash-verified source, strategy and report artifacts. Creation stages and verifies bytes before atomic directory registration. Reads verify manifest and artifact integrity. Explicit repeat adapters resolve pinned M2 snapshots and retain a new run even on mismatch; the M7 report is regenerated for each result. Stored source remains data and is never automatically executed.

Project snapshots are canonical ZIP archives with sorted relative paths, fixed metadata and no compression. They exclude VCS/runtime outputs plus the changing `coordination/` and `docs/validation/` evidence directories. Symlinks and junctions are rejected before traversal, including excluded paths. Replay compares strategy/project/data/config identities and M5 result metadata before recording. Current M5 is deterministic and does not accept an RNG argument; seed is retained as provenance and stochastic replay is deferred until the engine exposes a seeded interface.

## Verification

The full repository gate in [`checks-20260927T134136301859Z.json`](checks-20260927T134136301859Z.json) passed: Ruff lint, Ruff format, strict mypy over 40 source files, all **168 tests**, and `pip check`. Two symlink-creation integration cases were skipped because Windows symlink creation is unavailable for this account; equivalent mocked junction guards and ancestor/link checks ran. Integration verifies actual M2 snapshots, M5 backtests, M6 analytics and M7 report generation and M8 exact repeat/mismatch behavior.

Independent implementation review: PASS after the M8 contract documented the implemented source snapshot exclusions. The reviewer found no material store, replay, manifest, or path-integrity defect; review of the final provenance wording passed.

| Evidence | SHA-256 |
|---|---|
| `docs/m8-experiment-contract.md` | `C26D544F449995F8E6054A75C50C23CF8CC4212E292BD714365EE41E47B1AFA1` |
| `docs/adr/0003-experiment-reproducibility.md` | `A69CCD0A991A7948A8888C900BF9E471CB48C79E65793844BF3AC9B16050E4DA` |
| `src/quant_terminal/experiments/models.py` | `45410ECF5D36982FC4CBF14527AF3FD8931C3F28D3DE49500CF730CAF0912244` |
| `src/quant_terminal/experiments/source.py` | `BE1770EBDB406EE74024BE083B54B03ABE27BD450CFABFD18EC2C4250D153B18` |
| `src/quant_terminal/experiments/store.py` | `C00E345057989799433E7A769B937126155176487B19D06CA0FDDFA6091BFD41` |
| `tests/unit/test_experiment_models.py` | `C3E15EF961642C9757F353D26E6F76E40FCC57C0A302CD279DF22248292AD308` |
| `tests/unit/test_experiment_source.py` | `22F0AC89BDD56903F804F89CCDD8D97A0530BE96E7AF5283B1EF6BC2B491D98D` |
| `tests/integration/test_experiments.py` | `50AD0574C774B9404F6F88E5A46D4473A661732201DA5D2188D2F54967DCF4CF` |
| `docs/validation/checks-20260927T134136301859Z.json` | `6ABC91455FFBD0D25E4C4B176D6ADDC46B3CACD9A77A877C1DA43E5195D3A9F3` |
