# Remaining Verification Tasks

## Completed
- [x] **TanStack Start** — Verified server functions, server routes, file routing conventions, streaming patterns
- [x] **NautilusTrader** — Verified architecture (library not service), BacktestEngine/BacktestNode API, ParquetDataCatalog, one-node-per-process constraint, no REST/gRPC/SQLAlchemy
- [x] **Monaco Editor** — Verified Python has syntax colorization only (no IntelliSense/validation), onValidate doesn't fire for Python, Pyodide viable for client-side AST parsing

## Verified
- [x] **PyPortfolioOpt** — `add_sector_constraints(sector_mapper, sector_lower, sector_upper)` confirmed on `BaseConvexOptimizer`. Black-Litterman fully supported via `BlackLittermanModel` (absolute/relative views, Idzorek confidence method, posterior returns/covariance, `bl_weights()`). No built-in ESG-specific API, but generic `add_constraint()` handles any linear constraint. **Riskfolio-Lib** is a significantly more comprehensive alternative: 24 convex risk measures, Risk Parity, HRP/HERC hierarchical clustering, Black-Litterman (standard + Augmented + Bayesian), built-in constraint builders for asset classes/sectors/risk factors, and graph-based constraints. Consider Riskfolio-Lib over PyPortfolioOpt for production.
- [x] **Data Providers (Tiingo/Finnhub/Alpaca)** — **Tiingo:** Rate limits are plan-dependent (hourly + daily + monthly bandwidth, see pricing page); "50 req/hr" was incorrect. Has WebSocket streams for IEX (US equities intraday), Crypto, and Forex. EOD REST data goes back decades with split/dividend adjustments (CRSP methodology). Supports JSON and CSV response formats. **Finnhub:** Free tier: 60 calls/min + hard 30 calls/sec cap. **Stock Candles (OHLCV) and Tick Data are Premium-only** — free tier cannot fetch historical price data, only quotes, fundamentals, news, recommendations, and earnings calendar (1 month). WebSocket available for real-time trade streaming (free tier, 1 connection per API key). **Alpaca:** ~200 calls/min on free tier. WebSocket available for real-time market data. Supports 1min–1day bars for historical data.

## Not Started
- [ ] **TimescaleDB** — Verify hypertable configuration for OHLCV storage, chunk interval recommendations for financial data, compression policies, continuous aggregates for multi-timeframe rollups.
- [ ] **Redis** — Verify pub/sub vs Streams for real-time backtest progress broadcasting, Celery broker configuration, cache eviction strategies for market data.
- [ ] **Deployment Architecture** — Verify Vercel/Edge feasibility when backend is Python (Celery workers, NautilusTrader). TanStack Start on Vercel may need serverless functions that proxy to a separate Python backend. Evaluate if a single-server deployment (e.g., Granian serving TanStack Start SSR + FastAPI) is simpler for MVP.
- [ ] **WebSocket Support** — Verify how TanStack Start handles WebSocket connections for real-time backtest progress. The current diagram shows WebSocket but TanStack Start may not natively support WS — may need a separate endpoint via FastAPI or Socket.IO.
- [ ] **Custom Dataset Upload** — User story mentions "bring-your-own data" but system_design.md has no upload flow. Design file upload → validation → Parquet conversion → ParquetDataCatalog registration pipeline.
- [ ] **Strategy Sandboxing** — Verify Python sandboxing approach (RestrictedPython, nsjail, Docker isolation, or Pyodide server-side). Current doc says "restricted environment, no network access" but doesn't specify mechanism.
