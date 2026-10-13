# Portfolio and risk research

Open **Portfolio Research** in the local terminal. Select a model, edit its
synthetic parameter example, and choose **Calculate research**. Results include
the operation, local research mode, alignment and frequency assumptions, and
decimal-string output. Validation failures appear beside the request.

The same request is accepted by the headless CLI:

```powershell
uv run quant portfolio-research --request examples/portfolio-risk-parity.json
```

Or POST JSON to `/portfolio-research`; GET `/portfolio-research/capabilities`
lists supported operations. Parameters must exactly match the chosen native
function; financial numbers must be strings. Arrays align by input order.

| Operation | Inputs | Output convention |
| --- | --- | --- |
| volatility_target | weights, covariance, target; optional max_leverage | scaled weights; gross cap can prevent reaching target |
| risk_parity | covariance; optional tolerance, max_iterations | positive weights, sum one; equal variance contributions |
| mean_variance | expected_returns, covariance; optional risk_aversion | fully invested weights, shorts permitted |
| black_litterman | covariance, market_weights, views, view_returns, view_covariance; optional tau, risk_aversion | posterior expected excess returns, not weights |
| risk_contributions | weights, covariance | signed Euler contributions summing to volatility |
| factor_exposures | weights, loadings | portfolio factor loadings |
| factor_attribution | weights, loadings, factor_returns, residual_returns | additive factor/residual arithmetic contributions |
| factor_covariance | loadings, factor_covariance_matrix, idiosyncratic_variances | asset covariance from factor model |
| stress_scenarios | weights, asset_shocks, nav | scenario rows in input order, signed returns, NAV changes/losses |
| correlation_regimes | prior_returns, current_returns; optional threshold | sample correlations and maximum pair change classification |
| concentration | weights | gross-normalised HHI and reciprocal effective holdings |
| liquidity_capacity | position_values, adv_values, weights, participation_limit | fractional liquidation days and one-day capacity bottleneck |

Allocation covariance and expected excess returns are annual. Square roots and
division round within a fixed 34-digit context. Optimisers reject singular or
indefinite covariance; factor-model covariance allows PSD.
Allocation Cholesky pivots at or below 1e-30 of the original diagonal are rejected
as numerically unresolved; no covariance repair is applied.
Risk parity fails explicitly when its iteration budget cannot meet the specified tolerance.
Mean-variance solves only the sum-to-one utility problem; long-only, leverage,
turnover and transaction-cost constraints are unsupported. Black-Litterman uses
explicit positive-definite view uncertainty and fully invested nonnegative prior
weights. View returns and covariance must use consistent annual units.

Factor attribution is single-period simple return, not geometric linking. Missing
residual cash is treated as zero exposure/return. Concentration excludes residual
cash and normalises absolute invested weights. Correlation windows contain rows
of observations and columns of assets; zero variance is undefined and rejected.
Correlation labels describe these supplied windows, with an explicit threshold;
they do not forecast future regimes.

Scenario returns are supplied, with simple asset shocks at least -1. Positive
loss values are losses, negative losses are gains. Liquidity capacity assumes
constant supplied dollar ADV, identical participation limit and parallel asset
liquidation. Fractional days are volume-budget estimates; price impact, slippage,
settlement, execution timing and future liquidity are outside the calculation.
These outputs do not activate portfolio changes or broker orders.
