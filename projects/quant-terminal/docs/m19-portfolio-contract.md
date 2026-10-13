# M19 advanced portfolio and risk research

Owner requested M19 on 2026-10-02 after M18 passed. This milestone is pure local
research on supplied aligned data, exposed through the terminal API, CLI and UI.
It creates no trades, ledger changes or broker connections. M16 remains unpassed.

Allocation: annual covariance and annual expected excess returns, volatility
targeting with a gross leverage cap, equal-risk-contribution long-only risk parity,
fully invested unconstrained mean-variance utility optimisation (shorts allowed),
and Black-Litterman posterior expected returns with explicit view covariance.
There is no implied long-only mean-variance or turnover constraint; unsupported
arguments/modes must reject. Covariance optimisers require symmetric positive
definite matrices and reject singular/indefinite inputs; they do not silently repair
or regularise data. Iterative solvers must reject nonconvergence.
Allocation pivots at or below 1e-30 of the original diagonal also reject as
numerically unresolved under fixed precision.

Diagnostics: asset-factor loadings and factor covariance model, weighted exposure,
single-period arithmetic factor/residual return attribution, Euler volatility risk
contribution, ordered simple-return stress scenarios, sample correlation comparison,
gross-weight-normalised concentration and participation-limited liquidity capacity.
All dimensions align by input order. These modules do not download or fit market
data, infer crisis returns, estimate slippage or guarantee executable capacity.
Returns/volatility use fractions, not percentages. Stress shocks are user supplied;
correlation classifications use explicit thresholds and observations are historical.

The JSON seam accepts up to 34 coefficient digits and exponent magnitude 100;
native diagnostics use a narrower exponent magnitude 18 and magnitude 1e18 bound.
Model-specific bounds still apply after JSON conversion. Singular covariance is
allowed only in the factor covariance construction (PSD); optimisers require PD.

Financial inputs are finite Decimal values (JSON decimal strings), arithmetic uses
34-digit local precision, and inputs and matrix sizes are bounded. Outputs serialize
Decimals as strings. Undefined inputs such as zero-volatility correlation and empty
exposure reject explicitly. No float-to-Decimal financial conversion.

Gate requires independent mathematical fixtures for every capability, negative and
dimension/finite-input tests, API/CLI integration, frontend build/tests, full project
checks, independent review and documentation. No Paperclip employee participation
may be claimed without actual issue/run evidence. Collaborating implementation
agents are recorded separately from the 370 mapped employees.

References: He and Litterman's original exposition of Black-Litterman priors and
views, https://people.duke.edu/~charvey/Teaching/BA453_2006/GS_The_intuition_behind.pdf;
the authors' riskParityPortfolio documentation,
https://search.r-project.org/CRAN/refmans/riskParityPortfolio/html/riskParityPortfolio.html.
These establish model definitions, not future investment performance.
