# M19 portfolio and risk research acceptance

**Gate: PASS.** Independent source review and follow-up migration review passed.
The final frozen whole-project run `uv run python scripts/check.py` passed:
**538 tests passed, 32 skipped**, with Ruff lint/format, strict mypy (88 source
files) and dependency checks clean. Evidence:
`docs/validation/checks-20261002T202936006017Z.json`. The 32 skips comprise 30
opt-in M16 hostile-container cases and two Windows symlink limitations. One existing
Starlette test-client deprecation warning remains.

| Requirement | Public implementation | Mathematical evidence |
| --- | --- | --- |
| Volatility targeting | allocation.volatility_target | independent variance/square root, signed weights and gross cap |
| Risk parity | allocation.risk_parity | two-asset inverse volatility and correlated three-asset equal-risk budgets; nonconvergence |
| Mean-variance | allocation.mean_variance | hand-solved KKT and short-position fixture |
| Black-Litterman | allocation.black_litterman | independent prior/view posterior fixtures |
| Factor models | diagnostics.factor_covariance | B F B-transpose plus diagonal residual covariance, PSD cases |
| Factor exposure | diagnostics.factor_exposures | hand-calculated weighted loadings |
| Factor attribution | diagnostics.factor_attribution | independent additive factor/residual totals |
| Risk contribution | allocation.risk_contributions | Euler contributions sum to portfolio volatility, negative hedge contributions |
| Stress tests / scenario analysis | diagnostics.stress_scenarios | exact signed NAV changes/losses, residual cash and short exposure |
| Correlation regimes | diagnostics.correlation_regimes | positive/negative correlation windows and explicit threshold |
| Concentration | diagnostics.concentration | HHI, effective holdings, signed leverage and permutation |
| Liquidity / capacity | diagnostics.liquidity_capacity | dollar ADV/participation bottleneck, parallel liquidation and zero weights |

The terminal exposes the shared service at POST `/portfolio-research`, GET
`/portfolio-research/capabilities`, CLI `quant portfolio-research --request ...`
and the **Portfolio Research** workspace. The API/CLI integration uses an independent
two-asset 60%/40% allocation and confirms all twelve operations preserve expected
units. Malformed JSON, unsupported fields/modes, non-string/nonfinite/excessive
financial inputs and ambient-context variations are covered. M19 focused tests:
92 passed (35 allocation, 39 diagnostics, 18 integration).

Frontend build and 12 existing frontend tests passed. The build emits the existing
large Plotly chunk warning. Browser discovery returned no enabled browser; visual
UI smoke is unverified. A temporary local server was started for that attempt and
stopped. CLI example interaction returned weights 0.6/0.4. No broker connection,
order, strategy forward test or real account data was used.

Failed run evidence is preserved at `checks-20261002T202506352437Z.json`: 536 passed,
one reproducibility failure, 32 skipped. The coordinator edited the contract during
the run; provenance correctly detected changed project content. Files were frozen
before the subsequent gate. The earlier check `checks-20261002T202347555983Z.json`
passed 531 tests before the final six integration tests were added.

The frozen rerun `checks-20261002T202633616866Z.json` passed 536 tests and exposed
one genuine M18 concurrent schema-initialisation failure: duplicate `limit_price`
column migration. The coordinator assigned a minimal transaction-serialization
fix and regression to the M18 risk agent; final verification follows that fix.
The constructor now holds `BEGIN IMMEDIATE` across creation/migration and commits
only after integrity validation. Its 12-constructor/four-database regression and
five consecutive risk-suite runs passed; an independent follow-up review passed
with a fresh 33-test risk suite.

M16 stays unpassed. Model outputs are local research estimates, not approved capital
allocations. Mathematical limitations, covariance units, shorting semantics and
liquidity assumptions are documented in `docs/portfolio-research-usage.md` and
`docs/m19-portfolio-contract.md`. No all-employee interaction claim is made; real
Paperclip issue/run evidence was not produced in this milestone.
