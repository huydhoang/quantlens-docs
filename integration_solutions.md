# Integration Solutions

Proposed resolutions for each unresolved question in [integration_questions.md](integration_questions.md), informed by cross-referencing all architecture documents.

---

## Backend

### Backtest Execution: API Layer ↔ Huey ↔ NautilusTrader

#### 2.1 Frontend → backtest path — 🔴

**Recommendation: `Frontend → Raw ASGI API → Huey → Redis` is the canonical path. The `Tauri → Redis` arrow in the Deployment Architecture diagram is a diagram error.**

All backtest requests must go through the Raw ASGI API for:
- Input validation (using Pydantic directly, even without FastAPI's automatic request parsing)
- Strategy code lookup from PostgreSQL
- Job record creation in the database
- Rate limiting and resource management

The Deployment Architecture diagram in `ARCHITECTURE.md` (section "Local App (Docker Compose + Embedded)") has an `A -->|Enqueue Jobs| C` (Tauri → Redis) arrow that should be updated to `A -->|HTTP| API -->|Enqueue| C` to match the Backtest Execution Flow sequence diagram in the same document, which is the authoritative source.

#### 2.2 WebSocket progress ownership — 🟠

**Recommendation: The Raw ASGI API (Gunicorn+Uvicorn, port 8000) owns all WebSocket connections — both backtest progress and market data.**

The Backtest Execution Flow sequence diagram in `ARCHITECTURE.md` shows `API → UI` via WebSocket, placing progress broadcasting in the main API process. The pattern:
1. Huey worker publishes progress to a Redis channel (e.g., `backtest:{id}:progress`).
2. The Raw ASGI API subscribes to that channel and forwards messages to the connected WebSocket client.

All WebSocket traffic (backtest progress, market data, live signals) flows through the single Gunicorn+Uvicorn endpoint — there is no separate port or process for real-time data.

#### 2.3 NautilusTrader lifespan management in the API process — 🔴

**Recommendation: Do not initialize any NautilusTrader component in the Raw ASGI API lifespan. All NautilusTrader usage belongs in Huey workers.**

All docs agree: NautilusTrader is a library used in Huey workers, not in the API process. Any legacy examples showing a `NautilusKernel` in the API's lifespan context should be removed — they conflict with:
- `ARCHITECTURE.md`: "NautilusTrader is a library, not a service"
- `core_engine.md`: Backtests run in Huey process workers
- The one-`BacktestNode`-per-process constraint

The Raw ASGI API lifespan should initialize database pools (`asyncpg` for PostgreSQL/QuestDB), Redis connections, and DuckDB — not a NautilusTrader kernel. Strategy validation (dry-run) should also happen in a Huey task or subprocess, not in the API process.

#### 2.4 ProcessPoolExecutor vs Huey — 🟠

**Recommendation: Use `ProcessPoolExecutor` for short-lived CPU work (<5s) like skfolio optimization. Use Huey for long-running jobs (backtests, parameter sweeps, data ingestion).**

Decision boundary:

| Task | Executor | Rationale |
|------|----------|-----------|
| skfolio optimization | `ProcessPoolExecutor` | Completes in <5s, user expects synchronous response |
| Single backtest | Huey | Seconds to minutes, needs progress tracking |
| Parameter sweep | Huey (parallel dispatch via list comprehension) | Fan-out across workers |
| Data ingestion | Huey crontab | Scheduled, long-running |

To avoid conflicts with Gunicorn's `--workers` flag, use `ProcessPoolExecutor(max_workers=2)` (not 4) so that the total process count (Gunicorn workers + executor processes) stays within CPU core count. Alternatively, offload all CPU work to Huey and remove the executor entirely for a simpler architecture.

---

### Data Layer

#### 4.2 QuestDB write protocol — 🟠

**Recommendation: Use ILP over TCP (port 9009) for bulk ingestion; PGWire (port 8812) for reads and ad-hoc writes.**

| Protocol | Port | Use Case |
|----------|------|----------|
| ILP over TCP | 9009 | Bulk historical data ingestion (Tiingo/Alpaca batch downloads) — highest throughput |
| ILP over HTTP | 9000 | Alternative for environments where TCP sockets are inconvenient (e.g., serverless) — not primary |
| PGWire (asyncpg) | 8812 | All reads (`SAMPLE BY`, `LATEST ON` queries), ad-hoc single-row inserts for real-time ingestion |

For the data ingestion pipeline, prefer ILP over TCP. PGWire SQL INSERT is acceptable for low-throughput individual writes (e.g., a single real-time tick from a Gunicorn worker) but should not be used for bulk loads.

#### 4.4 PostgreSQL connection pools — 🟡

**Recommendation: Two separate `asyncpg` pools — one for PostgreSQL, one for QuestDB PGWire.**

```python
@asynccontextmanager
async def lifespan(app: Any):  # Raw ASGI scope dict; FastAPI not required
    app.state.pg_pool = await asyncpg.create_pool(dsn="postgresql://localhost:5432/quantlens")
    app.state.quest_pool = await asyncpg.create_pool(dsn="postgresql://localhost:8812/qdb")
    yield
    await app.state.pg_pool.close()
    await app.state.quest_pool.close()
```

Each pool is configured independently (different pool sizes, timeouts). No abstraction layer needed — the two databases serve different purposes and their pool configs will diverge.

---

### Real-Time Data Flow

#### 5.1 Data ingestion service — 🟠

**Recommendation: For MVP, data ingestion is a Huey crontab scheduled task running within the unified Gunicorn+Uvicorn stack.**

MVP data flow:
1. Huey's built-in crontab triggers periodic data ingestion tasks — nightly Tiingo EOD, weekly Alpaca intraday backfill. No separate Beat process is needed.
2. Ingestion tasks write to QuestDB via ILP over TCP.
3. No real-time streaming ingestion in MVP — backtest data is batch-loaded.

For live trading (future scope), real-time WebSocket streams from Finnhub/Alpaca can be managed as long-lived asyncio tasks within the Raw ASGI process, publishing to Redis and writing to QuestDB from within the same Gunicorn workers.

#### 5.2 Finnhub trade → OHLCV mismatch — 🟠

**Recommendation: Insert raw trades into a `trades` table; generate OHLCV bars via QuestDB's `SAMPLE BY`.**

The ingestion service should insert into a `trades` table (not `ohlcv_1m`):

```sql
INSERT INTO trades (timestamp, symbol, price, volume) VALUES ($1, $2, $3, $4);
```

OHLCV bars are derived on read using QuestDB's native `SAMPLE BY`:

```sql
SELECT timestamp, symbol,
       first(price) as open, max(price) as high,
       min(price) as low, last(price) as close,
       sum(volume) as volume
FROM trades
WHERE symbol = $1
SAMPLE BY 1m;
```

This is a natural fit for QuestDB's architecture and avoids pre-aggregation complexity in Python.

---

### Strategy Execution Security

#### 7.1 Sandboxing mechanism — 🟡

**Recommendation: For MVP (single-user local app), rely on Huey worker resource limits — no full sandboxing.**

The threat model for a local-first single-user app is **accidental harm** (infinite loops, excessive memory), not malicious code. MVP mitigations:
- `huey_consumer --workers 4 --worker-type process` with OS signal handlers for task time limits.
- Docker restart policy or OS supervisor for worker recycling (reclaim leaked memory).
- `resource.setrlimit()` in the Huey worker to cap memory usage per process.

Full sandboxing (RestrictedPython, nsjail, Pyodide server-side) should be deferred to the platform app where multi-tenant execution requires stronger isolation. Label this as "Future — Platform" in `ARCHITECTURE.md`.

---

### BacktestEngine vs BacktestNode

#### 11.1 API assignment — 🔴

**Recommendation: `BacktestNode` in Huey workers for all backtests. `BacktestEngine` is not used in production — it's a reference for understanding the low-level API.**

Rationale:
- `BacktestNode` with `BacktestRunConfig` is NautilusTrader's recommended production API.
- `task_queue.md`'s Huey example uses `BacktestNode`.
- `BacktestEngine.reset()` could theoretically reuse engines within process workers, but Docker restart policy or OS supervisor means workers restart periodically anyway. The complexity of engine reuse is not worth the marginal benefit.
- Update `ARCHITECTURE.md`'s class diagram to show `BacktestNode` instead of `BacktestEngine` in `NautilusBacktestService`.

For strategy validation (dry-run), do **not** use `BacktestEngine` in the Raw ASGI API process. Instead, validate in a short-lived Huey task that imports the user's strategy class and checks for required method signatures (`on_bar`, `on_start`, etc.) without running a simulation.

---

### Data Pipeline: QuestDB → Parquet → ParquetDataCatalog

#### 12.1 Export mechanism — 🔴

**Recommendation: On-demand export triggered before each backtest, not periodic or dual-write.**

Flow:
1. User submits a backtest request with symbols and date range.
2. The Raw ASGI API enqueues a Huey pipeline: `export_to_parquet(symbols, start, end).then(run_backtest, strategy_id, config)`.
3. The `export_to_parquet` task queries QuestDB via PGWire, converts to Parquet using PyArrow, and writes to the shared Parquet catalog directory.
4. The `run_backtest` task reads from the Parquet catalog via `ParquetDataCatalog`.

This ensures data is always fresh when a backtest starts, avoids stale Parquet files, and eliminates the need for Huey crontab scheduling or dual-write complexity. The export is fast (~seconds for typical date ranges) because QuestDB's columnar storage streams efficiently.

#### 12.2 Parquet catalog Docker volume — 🟠

**Recommendation: Define a named Docker volume shared between the data export task and Huey workers.**

```yaml
volumes:
  parquet-catalog:

services:
  worker:
    volumes:
      - parquet-catalog:/data/validated
  api:
    volumes:
      - parquet-catalog:/data/validated
```

Both the Raw ASGI API service (which triggers exports via Huey) and Huey workers (which read from the catalog) mount the same volume at `/data/validated`. This maps directly to the `ParquetDataCatalog(path="/data/validated")` shown in `data_providers.md`.

---

### Parameter Sweep Scalability

#### 13.1 Sweep duration — 🟡

**Recommendation: Accept ~10 min for a 500-combination sweep. Provide UI progress.**

With 4 workers and ~5s per backtest: 500 / 4 = 125 batches × 5s = ~10 minutes. This is acceptable for research iteration. Faster sweeps can be achieved by increasing `worker_concurrency` on machines with more cores.

UI progress: The sweep should publish aggregate progress to a Redis channel (`sweep:{id}:progress`), and the Raw ASGI API should forward it via WebSocket: `"234/500 complete, ~4 min remaining"`. Track completion counts using Redis pub/sub.

#### 13.2 Memory pressure — 🟡

**Recommendation: NautilusTrader uses memory-mapped Parquet files via Arrow, so memory overhead is manageable.**

`ParquetDataCatalog` reads Parquet files via PyArrow, which supports memory-mapped I/O. Multiple workers reading the same Parquet files on the shared volume will share OS-level page cache, not duplicate data in each process's heap.

For a 500-symbol × 20-year daily OHLCV dataset (~36M rows × ~48 bytes/row ≈ 1.7 GB on disk), expect ~200–500 MB per worker in practice (Arrow memory-maps lazily). With Docker restart policy or OS supervisor, workers restart and release memory periodically.

---

### Strategy Code: Authoring, Validation, and Execution

#### 14.1 Strategy template system — 🟠

**Recommendation: Provide 3–4 pre-built `.py` template files served via `GET /api/strategies/template?type={type}`.**

Templates:

| Template | Description |
|----------|-------------|
| `sma_crossover` | Simple Moving Average crossover — canonical example |
| `momentum` | Momentum strategy based on rate of change |
| `mean_reversion` | Mean reversion with Bollinger Bands |
| `blank` | Empty `TradingStrategy` subclass with all lifecycle methods stubbed |

Each template includes the full NautilusTrader boilerplate (`class MyStrategy(Strategy)`, `on_start`, `on_bar`, `on_stop`, `on_reset`) with inline comments explaining each method. The user fills in signal logic.

Monaco autocompletion: Register a `CompletionItemProvider` that suggests:
- NautilusTrader `Strategy` method names (`on_bar`, `on_quote_tick`, `submit_order`, etc.)
- `OrderFactory` methods (`market`, `limit`, `stop_market`)
- Common indicator constructors (`SMA`, `EMA`, `RSI`, `BollingerBands`)

#### 14.2 Strategy dry-run validation — 🟠

**Recommendation: Validation = Python import + introspection in a short-lived subprocess, not a BacktestEngine dry run.**

Steps:
1. Raw ASGI API receives `POST /api/strategies/validate` with Python source code.
2. API enqueues a Huey task `validate_strategy(code)`.
3. The Huey worker writes the code to a temp file, imports it in a subprocess with `importlib`, and checks:
   - Class inherits from `TradingStrategy`
   - Required methods exist (`on_start`, `on_bar` or `on_quote_tick`)
   - No syntax errors (already caught by Pyodide client-side, but double-checked)
4. Returns success/error to the API, which forwards to the frontend.

This avoids executing arbitrary code in the API process and avoids instantiating a `BacktestEngine` for validation. The subprocess provides basic isolation.

#### 14.3 Strategy code serialization — 🔴

**Recommendation: Store Python source in PostgreSQL (`STRATEGIES.python_code` column). Pass the strategy ID (not source code) to Huey. The worker loads the code from the database and `exec()`s it.**

Flow:
1. Monaco editor → `POST /api/strategies` → Raw ASGI API stores source in PostgreSQL → returns `strategy_id`.
2. `POST /api/backtest/run` → API enqueues `run_backtest(strategy_id, config)` (JSON-serializable).
3. Huey worker receives `strategy_id`, queries PostgreSQL for the Python source, `exec()`s it to define the class, and registers it with `BacktestNode`.

```python
@huey.task()
def run_backtest(strategy_id: str, config: dict):
    code = db.fetch_strategy_code(strategy_id)  # From PostgreSQL
    namespace = {}
    exec(code, namespace)
    strategy_cls = namespace["MyStrategy"]  # Convention: class must be named MyStrategy
    node = BacktestNode(configs=build_config(strategy_cls, config))
    node.run()
    return node.get_results()
```

**Security note:** `exec()` runs arbitrary Python in the Huey worker process. For the single-user local MVP, this is acceptable (the user is running their own code on their own machine). For the future multi-tenant platform, replace `exec()` with a sandboxed execution environment (see §7.1). As an interim measure, consider restricting built-ins: `exec(code, {"__builtins__": safe_builtins}, namespace)` to prevent accidental use of `os`, `subprocess`, etc.

This keeps Huey task arguments small (just IDs) and avoids passing Python source through the message queue.

---

### NautilusTrader Data Types vs Provider Data

#### 15.1 Data type conversion stage — 🟠

**Recommendation: Convert at Parquet catalog write time (during export from QuestDB), not at ingestion or read time.**

Pipeline:
1. **Ingestion:** Provider → raw JSON → normalized to QuantLens schema (UTC timestamps, decimal prices) → QuestDB (QuantLens-native schema).
2. **Export:** QuestDB → Parquet files → convert to NautilusTrader `Bar` types using a custom adapter → write to `ParquetDataCatalog` in NautilusTrader's expected schema.
3. **Read:** `ParquetDataCatalog` reads Parquet files that are already in NautilusTrader format — no conversion needed at read time.

This approach:
- Keeps QuestDB storage format independent of NautilusTrader (no lock-in).
- Ensures the Parquet catalog is always in NautilusTrader-native format (fast reads during backtests).
- Centralizes conversion logic in a single adapter used during export.

---

### skfolio Integration Boundary

#### 17.1 NautilusTrader → skfolio handoff — 🟠

**Recommendation: A Huey task converts NautilusTrader results to asset-return DataFrames for skfolio. The conversion is strategy-level, not asset-level.**

Flow:
1. User runs N backtests (one per strategy or parameter set), each producing an equity curve.
2. User requests portfolio optimization via `POST /api/optimize`.
3. Raw ASGI API retrieves equity curves from PostgreSQL (`RESULTS.equity_curve`).
4. A conversion function computes daily returns from each equity curve, producing a DataFrame where rows = dates, columns = strategy names.
5. This DataFrame is fed to skfolio's `MeanRisk` optimizer.
6. The optimizer returns allocation weights across strategies.

```python
def backtest_results_to_returns(result_ids: list[str]) -> pd.DataFrame:
    """Convert NautilusTrader equity curves to a returns DataFrame for skfolio."""
    returns = {}
    for rid in result_ids:
        equity = db.fetch_equity_curve(rid)  # List of {date, value} dicts
        series = pd.Series({e["date"]: e["value"] for e in equity})
        returns[rid] = series.pct_change().dropna()
    return pd.DataFrame(returns)
```

This runs in the Raw ASGI API process (via `ProcessPoolExecutor`) since it's a quick computation, or as a Huey task for large portfolios.

---

### Data Provider Contradictions

#### 9.1 Tiingo rate limits — 🟠

**Recommendation: Update `data_providers.md` to state that Tiingo free-tier limits are plan-dependent and refer to the pricing page.**

Tiingo's free-tier limits are not a simple "50 req/hr" — they vary based on the plan and endpoint. The `ARCHITECTURE.md` phrasing is more accurate: "Tiingo limits are plan-dependent (hourly requests + daily requests + monthly bandwidth — see pricing page)." The README already states "1,000 req/day" which aligns with the documented daily limit.

Correct the `data_providers.md` table row for Tiingo to:
```
| Tiingo | Plan-dependent (see pricing page) · ~1,000 req/day (free) · 500 unique symbols/month | ...
```

Remove the specific "50 requests/hour" claim, which was identified as incorrect in `todos.md`. Also update the `RateLimitedClient` example in `data_providers.md` (which references `max_per_hour=50`) to use `max_per_day=1000` instead, and cross-reference `ARCHITECTURE.md`'s data management section for consistency.

---

### Live/Paper Trading Path

#### 18.1 Scope — 🟢

**Recommendation: Live and paper trading are explicitly future scope. Label them as such in `core_engine.md`.**

For MVP, QuantLens is a **backtest-only** tool. The backtest-live parity claim in `core_engine.md` is a value proposition for the platform — it means users won't need to rewrite strategies when live trading is eventually added.

Add a note to `core_engine.md`:
> **MVP Scope:** QuantLens v1 supports backtesting only. Live and paper trading via broker adapters (Binance, Interactive Brokers, etc.) are planned for a future release. The architecture is designed so that strategies written for backtesting will run in live mode with zero code changes when this feature is added.

---

## Frontend

### Frontend ↔ API Layer Communication

#### 1.1 API process ownership — 🔴

**Recommendation: The Raw ASGI API runs inside Docker Compose, not managed by Tauri.**

The `ARCHITECTURE.md` Deployment Architecture diagram is authoritative — the Raw ASGI API (Gunicorn+Uvicorn), Huey workers, PostgreSQL, QuestDB, and Redis all run as Docker Compose services. Tauri is a native desktop app that connects to these services over `localhost`.

- **Port binding:** The Raw ASGI API exposes port 8000 on the host via Docker Compose `ports` mapping.
- **Startup orchestration:** `docker compose up` starts all backend services. Tauri connects to them on launch.
- **Dev workflow:** Mount the Raw ASGI source directory as a Docker volume and run with `--reload` inside the container for hot-reload.
- Update `local_frontend.md`'s diagram to clarify that the `Gunicorn+Uvicorn Raw ASGI (Localhost)` connection is via Docker's host-mapped port, not a bare process.

#### 1.2 Service discovery — 🟡

**Recommendation: Hardcode ports in a frontend config module; use environment variables for overrides.**

For a local-first single-user app, the hardcoded default (`localhost:8000` for the single Gunicorn+Uvicorn endpoint) is pragmatic. Create a `src/lib/config.ts` that reads environment variables at build time:

```typescript
export const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
export const WS_BASE = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000";
```

For production or TLS-enabled deployments, override these with `https://` and `wss://` protocols via environment variables.

Tauri IPC is unnecessary for service discovery — the backend services have fixed, known ports.

#### 1.3 CORS configuration — 🟡

**Recommendation: Allow both the Vite dev origin and the Tauri production origin.**

```python
allow_origins=[
    "http://localhost:3000",      # Vite dev server
    "http://localhost:1420",      # Tauri dev server (default Tauri port)
    "https://tauri.localhost",    # Tauri production webview
    "tauri://localhost",          # Tauri production webview (alternative scheme)
]
```

Since everything runs locally on the same machine, the security risk is minimal. Alternatively, a Tauri Rust-side proxy could eliminate CORS entirely, but the multi-origin allowlist is simpler for MVP.

---

### Tauri Integration

#### 6.1 Tauri Rust backend usage — 🟡

**Recommendation: Use Tauri as a lightweight WebView shell for MVP, with one `#[tauri::command]` for Docker health checks.**

MVP Tauri commands:
- `check_backend_health()` — pings `http://localhost:8000/health` and surfaces connection status in the UI.

Future Tauri commands (post-MVP):
- File operations (save/load strategy files locally)
- Native OS notifications (backtest complete)
- System tray integration

All business logic stays in the React SPA + Raw ASGI backend. Tauri's Rust backend is used only for desktop-native features that the WebView cannot provide.

#### 6.2 Startup orchestration — 🟠

**Recommendation: Option 3 — a launcher script or Tauri startup hook runs `docker compose up -d` before the WebView loads.**

Sequence:
1. User launches the Tauri app.
2. Tauri's Rust `setup()` hook runs `docker compose up -d` in the background.
3. The React SPA shows a "Connecting to backend..." spinner.
4. The SPA polls `http://localhost:8000/health` with exponential backoff (1s, 2s, 4s, max 30s).
5. Once the health check passes, the SPA renders the main dashboard.
6. If services aren't available after 60s, show an error with a "Retry" button and instructions to run `docker compose up` manually.

---

### Real-Time Data Delivery

#### 5.3 Frontend market data delivery — 🟡

**Recommendation: WebSocket updates pushed into TanStack Query cache, as described in `local_frontend.md`.**

This is the pattern already documented:
1. REST via TanStack Query for initial data load and CRUD operations.
2. A single WebSocket connection for real-time updates (backtest progress, market data).
3. WebSocket messages pushed into TanStack Query cache via `queryClient.setQueryData()`.

No REST polling needed. The WebSocket + TanStack Query cache pattern provides a unified state model.

---

### Custom Dataset Upload

#### 10.1 Custom dataset upload — 🟡

**Recommendation:**

- **File formats:** CSV and Parquet (MVP). Excel support deferred.
- **Validation:** Required columns: `timestamp`, `open`, `high`, `low`, `close`, `volume`. Timestamp parsed as UTC. Rows sorted by timestamp. Reject files with >5% missing values.
- **Storage:** Upload → Raw ASGI API validates → writes to QuestDB (for querying) and exports to Parquet (for `ParquetDataCatalog`).
- **UI:** Simple file upload button in the Tauri app. Drag-and-drop is a nice-to-have for post-MVP.
- **Endpoint:** `POST /api/data/upload` with `multipart/form-data`.

---

## Others

### Platform App Integration (Future)

#### 8.1 Local → platform data flow — 🟢

**Recommendation: Submit aggregated results (metrics + equity curves), not raw trades or strategy code.**

Proposed data contract:
- **Submitted:** Backtest metrics (Sharpe, max drawdown, total return, win rate), equity curve data points, strategy metadata (name, description, asset universe, time period).
- **Not submitted:** Raw trade logs, strategy source code (proprietary to the user).
- **Auth:** OAuth 2.0 / JWT — the local app authenticates with the platform via a login flow in the Tauri WebView.
- **API:** REST endpoints on the platform app (`POST /api/results/submit`).

This is explicitly future scope. Defer detailed API contract design until the platform app architecture is defined.

---

### Authentication and Authorization

#### 10.2 Authentication model — 🟡

**Recommendation: No authentication for the local app MVP.**

The `USERS` table and JWT auth are for the future platform app. A single-user desktop app running on `localhost` does not need auth. Other processes on the same machine *can* access the API — this is acceptable for a personal research tool.

For MVP, hardcode a single default user ID in the database. Add auth when the platform app is built.

---

### Error Handling and Retry Strategy

#### 10.3 Error handling / retry strategy — 🟡

**Recommendation:**

| Failure Type | Strategy |
|---|---|
| Data provider connection failure | Exponential backoff (1s, 2s, 4s, 8s, max 60s) with circuit breaker (5 failures → 5min cooldown). Log and surface in UI. |
| Backtest failure | Do not auto-retry. Report error to user via WebSocket with stack trace. User decides whether to fix and re-run. |
| QuestDB write failure | Retry 3x with 1s delay. On persistent failure, log the dropped data and alert the user. Do not buffer in Redis (adds complexity for a rare failure mode). |
| Frontend WebSocket disconnect | Auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s). Show "Reconnecting..." banner in UI. On reconnect, re-fetch current state via REST. |
