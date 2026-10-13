# M11 Monte Carlo and robustness engine gate

Status: **PASSED** on 2026-09-27.

## Delivered

Implemented deterministic trade, IID periodic-return, and moving-block bootstrap paths with a versioned SHA-256 rejection sampler. Immutable specifications bind source run IDs/fingerprint, exact observations and segment IDs, method parameters, currency, annualization basis, seed, limits, and ruin threshold. Canonical artifacts bind the complete specification, sample indices, path checkpoints, metrics, and empirical distributions for retention in M8.

Path calculations use isolated Decimal precision 34 and `ROUND_HALF_EVEN`. Outputs include terminal wealth, CAGR, Sharpe, maximum drawdown, drawdown duration, CVaR95 when the M6 minimum sample and tail counts are satisfied, and optional threshold-crossing ruin probability. Undefined metrics remain null with reasons. Leveraged returns below -100% are retained and can make wealth nonpositive; CAGR is then unavailable. Trade-bootstrap annualized metrics explicitly depend on an assumed trade frequency.

Moving-block bootstrap respects explicit per-return segment IDs and requires them when several source runs are combined. The M8/M10 source adapter must map observations to segments, mark missing-data boundaries, and verify currency compatibility against the pinned specification; the opaque source fingerprint alone cannot prove those mappings. This remains an adapter conformance requirement.

## Verification

The full gate in [`checks-20260927T163823723920Z.json`](checks-20260927T163823723920Z.json) passed: Ruff lint, Ruff format, mypy over 51 source files, **191 passed**, `pip check` clean. Two existing symlink tests were skipped because symlink creation is unavailable on this Windows account. The independent final quant review passed all reviewed M11 code and method semantics; its focused suite passed 7 tests.

## Evidence hashes

| Artifact | SHA-256 |
|---|---|
| `docs/m11-monte-carlo-contract.md` | `ACA126B34252981D8AB307BB0486A2B861E1334F4795BB01CD85184CF1A1E27A` |
| `src/quant_terminal/monte_carlo/models.py` | `EBF25AD1E2917B3B5E6681C1592DB52CA15B9ECE820744DE43A64E25B153CCB9` |
| `src/quant_terminal/monte_carlo/engine.py` | `770483B55352E7F7E991BDE407277663380E49596FC16957C1FDA5D13E005E53` |
| `tests/unit/test_monte_carlo.py` | `A31CC06F787E891DA167C92DD1AC59A0AD3BCB2C32B848F64483569F764209FD` |
| `docs/validation/checks-20260927T163823723920Z.json` | `24AE5681DDF33AEB114239A7E8B610B2D2EA335436F21C04518224EB5DB88699` |
