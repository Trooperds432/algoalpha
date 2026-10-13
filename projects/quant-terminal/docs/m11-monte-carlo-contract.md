# M11 Monte Carlo and robustness engine contract

## Objective

Quantify uncertainty conditional on an explicit historical sample and resampling model. A Monte Carlo result describes paths generated under its assumptions; it is not a forecast, return guarantee, or independent proof of strategy edge. M11 operates on registered M8/M10 samples and does not submit orders.

## Input identity

An immutable simulation specification pins the M8 run or M10 OOS-only run IDs, source fingerprints, dataset/sample fingerprint, exact input series, uppercase ISO 4217 currency code, frequency, seed, path count, path length, method, and every method parameter. The currency is part of the simulation fingerprint and combined artifact; the M8/M10 adapter must confirm that every selected source run has that same currency before constructing a specification. Input observations are finite, ordered, and typed. Returns below -100% are retained for short or leveraged trade outcomes; compounding may create nonpositive wealth, which remains explicit and invalidates CAGR. The simulation rejects empty or incompatible samples, unsupported frequency conversions, and invalid parameters. It never silently substitutes an alternate sample.

## Supported resampling methods

- Trade bootstrap samples complete closed trade outcomes with replacement and preserves the declared trade-return convention. Open positions are excluded unless a future version defines a marked-position protocol.
- IID return bootstrap samples individual periodic returns with replacement and assumes exchangeability/independence for the generated paths. It must display that assumption.
- Moving-block bootstrap samples contiguous blocks from the supplied return series. Block length and edge handling are explicit and fingerprinted; blocks do not cross missing-data gaps or source-sample boundaries.

Each method preserves the input order and the exact sampling indices in deterministic output identity. Seeded generators use a named, versioned deterministic algorithm that does not depend on Python's global random state, process hash randomisation, thread scheduling, or ambient Decimal context. Identical specification and sample fingerprints reproduce identical paths and metrics.

## Path construction and metrics

Every path begins from an explicit positive starting equity and applies sampled returns using isolated Decimal precision 34 and ROUND_HALF_EVEN. Non-finite values, nonpositive equity, and insolvency are retained as explicit states; CAGR and log-return metrics are unavailable where their domain is invalid. Do not repair or clamp paths.

Calculate and retain path-level terminal wealth, CAGR, Sharpe, maximum drawdown, drawdown duration, and empirical CVaR using the M6 formulas and edge-case rules. The annual risk-free rate and periods per year are explicit; M6's log-converted per-period risk-free return is used for Sharpe. Trade-bootstrap specifications require a separate declared `trade_frequency_per_year`; their annualized CAGR and Sharpe are explicitly conditional on sequential trades at that assumed frequency, not elapsed-time M6 backtest CAGR. The optional risk-of-ruin probability is the fraction of simulated paths crossing the declared equity threshold within the configured path length; it is not M6's trade-allocation risk-of-ruin estimator. Both threshold and horizon are explicit. Undefined or insufficient metrics remain null with reasons. CVaR95 is unavailable below the M6 minimum of 20 observations and five expected tail observations. Summaries include p05, p25, p50, p75, and p95 with the quantile convention pinned to M6; include valid and invalid path counts.

## Output and provenance

Emit one combined canonical JSON artifact binding the complete immutable specification and result, including source run IDs/fingerprint, exact return sample and segment IDs, method/sample kind, algorithm version, seed, annualization assumptions, path count, deterministic draw indices, full path checkpoints, metrics, quantile summaries, and limitations. These bytes are suitable for retention as immutable M8 artifacts; M11 does not add a parallel experiment database. The path and distribution series provide chart-ready data for a fan chart, terminal-wealth distribution, maximum-drawdown histogram and CDF, Sharpe distribution, and optional model-conditional risk-of-ruin display. Every chart must state method, sample, seed, path count, units, and assumptions.

## Out of scope

Parameter, slippage, execution-delay, start/end-date, asset-universe perturbations; residual and correlated multivariate bootstrap; multiple-testing correction; Deflated/Probabilistic Sharpe; and White's Reality Check are M12 or later. Do not imply IID paths are independent strategy evidence or use Monte Carlo percentiles as production approval thresholds.

## Verification and exit gate

Test method-specific index selection, deterministic replay across process and Decimal contexts, path arithmetic against hand-calculated fixtures, trade grouping, block boundaries and missing-data rejection, metric edge cases/insolvency, quantile conventions, manifest/sample tamper rejection, output reproduction, and chart labeling. The exit gate requires independently reviewed method semantics and byte-identical simulation path/metric fingerprints for identical seeds and configurations.
