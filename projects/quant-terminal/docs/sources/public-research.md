# Public implementation references

Checked 2026-09-23 through native web research (Empire public research workflow).

- [LEAN reality modeling](https://www.quantconnect.com/docs/v2/writing-algorithms/reality-modeling/key-concepts): separates execution assumptions into models. Our architecture adopts explicit fill/fee/slippage boundaries; it does not copy engine implementation.
- [LEAN time model](https://www.quantconnect.com/docs/v1/key-concepts/understanding-time): historical data is presented in temporal order. Our specification additionally separates economic fill time from completed-bar observation time.
- [DuckDB Parquet support](https://duckdb.org/docs/lts/data/parquet/overview): supports direct analytical Parquet reads and pushdown. This supports the local storage choice, without requiring a database service.
- [FastAPI worker concepts](https://fastapi.tiangolo.com/deployment/server-workers/): worker processes support process-level concurrency. Backtest jobs will be separately bounded, not unlimited tasks on an API request loop.

The PDF's vendor pricing, licensing and regulatory summaries are source context, not
newly verified claims or purchase recommendations. Recheck applicable dependency
licenses before incorporation/distribution. No external paid-model call was made.
Smart Router local preview returned gpt-5.6-terra/medium; no model switch occurred.
The in-app Browser was unavailable; no browser visual verification is claimed.
