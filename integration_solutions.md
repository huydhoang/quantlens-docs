# Integration Solutions

Proposed resolutions for each unresolved question in [integration_questions.md](integration_questions.md), informed by cross-referencing all architecture documents.

---

## 1. Frontend ↔ API Layer Communication

### 1.1 API process ownership — 🔴

**Recommendation: FastAPI runs inside Docker Compose, not managed by Tauri.**

The `system_design.md` Deployment Architecture diagram is authoritative — FastAPI, Celery workers, PostgreSQL, QuestDB, and Redis all run as Docker Compose services. Tauri is a native desktop app that connects to these services over `localhost`.

- **Port binding:** FastAPI exposes port 8000 on the host via Docker Compose `ports` mapping.
- **Startup orchestration:** `docker compose up` starts all backend services. Tauri connects to them on launch.
- **Dev workflow:** Mount the FastAPI source directory as a Docker volume and run `uvicorn --reload` inside the container for hot-reload.
- Update `local_frontend.md`'s diagram to clarify that the `FastAPI + Uvicorn (Localhost)` connection is via Docker's host-mapped port, not a bare process.

### 1.2 Service discovery — 🟡

**Recommendation: Hardcode ports in a frontend config module; use environment variables for overrides.**

For a local-first single-user app, hardcoded defaults (`localhost:8000` for Tier 1, `localhost:8001` for Tier 2 when added) are pragmatic. Create a `src/lib/config.ts` that reads environment variables at build time:

```typescript
export const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
export const WS_BASE = import.meta.env.VITE_WS_URL ?? "ws://localhost:8000";
```

Tauri IPC is unnecessary for service discovery — the backend services have fixed, known ports.

### 1.3 CORS configuration — 🟡

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

## 2. Backtest Execution: FastAPI ↔ Celery ↔ NautilusTrader

### 2.1 Frontend → backtest path — 🔴

**Recommendation: `Frontend → FastAPI → Celery → Redis` is the canonical path. The `Tauri → Redis` arrow in the Deployment Architecture diagram is a diagram error.**

All backtest requests must go through FastAPI for:
- Pydantic validation of backtest configs
- Strategy code lookup from PostgreSQL
- Job record creation in the database
- Rate limiting and resource management

The Deployment Architecture diagram's `A -->|Enqueue Jobs| C` (Tauri → Redis) arrow should be updated to `A -->|HTTP| API -->|Enqueue| C` to match the Backtest Execution Flow sequence diagram, which is the authoritative source.

### 2.2 WebSocket progress ownership — 🟠

**Recommendation: Tier 1 (FastAPI on port 8000) owns backtest progress WebSocket for MVP.**

The Backtest Execution Flow sequence diagram in `system_design.md` shows `API → UI` via WebSocket, placing progress broadcasting in FastAPI. The pattern:
1. Celery worker publishes progress to a Redis channel (e.g., `backtest:{id}:progress`).
2. FastAPI subscribes to that channel and forwards messages to the connected WebSocket client.

This keeps MVP single-tier. When Tier 2 is added for live trading, market data WebSockets move to port 8001, but backtest progress stays in Tier 1 since it's tied to the request lifecycle.

### 2.3 NautilusKernel in FastAPI lifespan — 🔴

**Recommendation: Remove `NautilusKernel` from FastAPI's lifespan. It is a mistake.**

All docs agree: NautilusTrader is a library used in Celery workers, not in the API process. The `NautilusKernel` in `asgi_web_server.md`'s Tier 1 example is incorrect — it conflicts with:
- `system_design.md`: "NautilusTrader is a library, not a service"
- `core_engine.md`: Backtests run in Celery prefork workers
- The one-`BacktestNode`-per-process constraint

FastAPI's lifespan should initialize database pools (`asyncpg` for PostgreSQL/QuestDB), Redis connections, and DuckDB — not a NautilusTrader kernel. Strategy validation (dry-run) should also happen in a Celery task or subprocess, not in the FastAPI process.

### 2.4 ProcessPoolExecutor vs Celery — 🟠

**Recommendation: Use `ProcessPoolExecutor` for short-lived CPU work (<5s) like skfolio optimization. Use Celery for long-running jobs (backtests, parameter sweeps, data ingestion).**

Decision boundary:

| Task | Executor | Rationale |
|------|----------|-----------|
| skfolio optimization | `ProcessPoolExecutor` | Completes in <5s, user expects synchronous response |
| Single backtest | Celery | Seconds to minutes, needs progress tracking |
| Parameter sweep | Celery (`group`) | Fan-out across workers |
| Data ingestion | Celery Beat | Scheduled, long-running |

To avoid conflicts with `uvicorn --workers`, use `ProcessPoolExecutor(max_workers=2)` (not 4) so that the total process count (Uvicorn workers + executor processes) stays within CPU core count. Alternatively, offload all CPU work to Celery and remove the executor entirely for a simpler architecture.

---

## 3. Two-Tier Architecture

### 3.1 MVP scope — 🔴

**Recommendation: MVP is single-tier (FastAPI on port 8000 only). Tier 2 is explicitly future scope, to be added when live trading features are implemented.**

`asgi_web_server.md`'s own final verdict says: "start with FastAPI on Uvicorn. When live trading is added, extract real-time endpoints." Both `system_design.md` and `local_frontend.md` show only a single API layer. Label Tier 2 code in `asgi_web_server.md` as "Future — Live Trading" to avoid confusion.

### 3.2 Shared NautilusTrader kernel — 🟠

**Recommendation: The "shared kernel" in the two-tier diagram is a misconception. Each tier runs its own process; NautilusTrader instances are per-process.**

When Tier 2 is implemented:
- Tier 2 does not need a full `BacktestNode`. It uses lightweight NautilusTrader components (data types, indicator calculations) — or processes ticks without NautilusTrader at all, using custom signal logic.
- The "shared layer" is Redis pub/sub (for cross-tier communication) and shared databases (QuestDB, PostgreSQL), not a shared in-process kernel.
- Update the two-tier diagram to remove "NautilusTrader kernel" from the shared layer and replace with "Redis pub/sub · Shared databases."

---

## 4. Data Layer

### 4.2 QuestDB write protocol — 🟠

**Recommendation: Use ILP over TCP (port 9009) for bulk ingestion; PGWire (port 8812) for reads and ad-hoc writes.**

| Protocol | Port | Use Case |
|----------|------|----------|
| ILP over TCP | 9009 | Bulk historical data ingestion (Tiingo/Alpaca batch downloads) — highest throughput |
| ILP over HTTP | 9000 | Alternative for environments where TCP sockets are inconvenient (e.g., serverless) — not primary |
| PGWire (asyncpg) | 8812 | All reads (`SAMPLE BY`, `LATEST ON` queries), ad-hoc single-row inserts from Tier 2 |

The Tier 2 code in `asgi_web_server.md` that uses `pool.execute("INSERT INTO ohlcv_1m ...")` is acceptable for individual tick writes but suboptimal for bulk ingestion. For the data ingestion pipeline, prefer ILP over TCP.

### 4.4 PostgreSQL connection pools — 🟡

**Recommendation: Two separate `asyncpg` pools — one for PostgreSQL, one for QuestDB PGWire.**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pg_pool = await asyncpg.create_pool(dsn="postgresql://localhost:5432/quantlens")
    app.state.quest_pool = await asyncpg.create_pool(dsn="postgresql://localhost:8812/qdb")
    yield
    await app.state.pg_pool.close()
    await app.state.quest_pool.close()
```

Each pool is configured independently (different pool sizes, timeouts). No abstraction layer needed — the two databases serve different purposes and their pool configs will diverge.

---

## 5. Real-Time Data Flow

### 5.1 Data ingestion service — 🟠

**Recommendation: For MVP, data ingestion is a Celery Beat scheduled task, not a separate service. When Tier 2 is added, the vanilla ASGI gateway handles both ingestion and serving.**

MVP data flow:
1. Celery Beat triggers periodic data ingestion tasks (nightly Tiingo EOD, weekly Alpaca intraday backfill).
2. Ingestion tasks write to QuestDB via ILP over TCP.
3. No real-time streaming ingestion in MVP — backtest data is batch-loaded.

When Tier 2 is added, the vanilla ASGI gateway ingests real-time WebSocket streams (Finnhub, Alpaca) and writes to QuestDB while simultaneously serving data to the React frontend. This collapses ingestion and serving into a single process, which is the design shown in `asgi_web_server.md`.

### 5.2 Finnhub trade → OHLCV mismatch — 🟠

**Recommendation: Insert raw trades into a `trades` table; generate OHLCV bars via QuestDB's `SAMPLE BY`.**

The Tier 2 code should insert into a `trades` table (not `ohlcv_1m`):

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

### 5.3 Frontend market data delivery — 🟡

**Recommendation: WebSocket updates pushed into TanStack Query cache, as described in `local_frontend.md`.**

This is the pattern already documented:
1. REST via TanStack Query for initial data load and CRUD operations.
2. A single WebSocket connection for real-time updates (backtest progress, market data).
3. WebSocket messages pushed into TanStack Query cache via `queryClient.setQueryData()`.

No REST polling needed. The WebSocket + TanStack Query cache pattern provides a unified state model.

---

## 6. Tauri Integration

### 6.1 Tauri Rust backend usage — 🟡

**Recommendation: Use Tauri as a lightweight WebView shell for MVP, with one `#[tauri::command]` for Docker health checks.**

MVP Tauri commands:
- `check_backend_health()` — pings `http://localhost:8000/health` and surfaces connection status in the UI.

Future Tauri commands (post-MVP):
- File operations (save/load strategy files locally)
- Native OS notifications (backtest complete)
- System tray integration

All business logic stays in the React SPA + FastAPI. Tauri's Rust backend is used only for desktop-native features that the WebView cannot provide.

### 6.2 Startup orchestration — 🟠

**Recommendation: Option 3 — a launcher script or Tauri startup hook runs `docker compose up -d` before the WebView loads.**

Sequence:
1. User launches the Tauri app.
2. Tauri's Rust `setup()` hook runs `docker compose up -d` in the background.
3. The React SPA shows a "Connecting to backend..." spinner.
4. The SPA polls `http://localhost:8000/health` with exponential backoff (1s, 2s, 4s, max 30s).
5. Once the health check passes, the SPA renders the main dashboard.
6. If services aren't available after 60s, show an error with a "Retry" button and instructions to run `docker compose up` manually.

---

## 7. Strategy Execution Security

### 7.1 Sandboxing mechanism — 🟡

**Recommendation: For MVP (single-user local app), rely on Celery worker resource limits — no full sandboxing.**

The threat model for a local-first single-user app is **accidental harm** (infinite loops, excessive memory), not malicious code. MVP mitigations:
- `task_time_limit=3600` and `task_soft_time_limit=3000` in Celery config (already specified).
- `worker_max_tasks_per_child=50` to restart workers and reclaim leaked memory.
- `resource.setrlimit()` in the Celery worker to cap memory usage per process.

Full sandboxing (RestrictedPython, nsjail, Pyodide server-side) should be deferred to the platform app where multi-tenant execution requires stronger isolation. Label this as "Future — Platform" in `system_design.md`.

---

## 8. Platform App Integration (Future)

### 8.1 Local → platform data flow — 🟢

**Recommendation: Submit aggregated results (metrics + equity curves), not raw trades or strategy code.**

Proposed data contract:
- **Submitted:** Backtest metrics (Sharpe, max drawdown, total return, win rate), equity curve data points, strategy metadata (name, description, asset universe, time period).
- **Not submitted:** Raw trade logs, strategy source code (proprietary to the user).
- **Auth:** OAuth 2.0 / JWT — the local app authenticates with the platform via a login flow in the Tauri WebView.
- **API:** REST endpoints on the platform app (`POST /api/results/submit`).

This is explicitly future scope. Defer detailed API contract design until the platform app architecture is defined.

---

## 9. Data Provider Contradictions

### 9.1 Tiingo rate limits — 🟠

**Recommendation: Update `data_providers.md` to state that Tiingo free-tier limits are plan-dependent and refer to the pricing page.**

Tiingo's free-tier limits are not a simple "50 req/hr" — they vary based on the plan and endpoint. The `system_design.md` phrasing is more accurate: "Tiingo limits are plan-dependent (hourly requests + daily requests + monthly bandwidth — see pricing page)." The README already states "1,000 req/day" which aligns with the documented daily limit.

Correct the `data_providers.md` table row for Tiingo to:
```
| Tiingo | Plan-dependent (see pricing page) · ~1,000 req/day (free) · 500 unique symbols/month | ...
```

Remove the specific "50 requests/hour" claim, which was identified as incorrect in `todos.md`.

---

## 10. Missing Specifications

### 10.1 Custom dataset upload — 🟡

**Recommendation:**

- **File formats:** CSV and Parquet (MVP). Excel support deferred.
- **Validation:** Required columns: `timestamp`, `open`, `high`, `low`, `close`, `volume`. Timestamp parsed as UTC. Rows sorted by timestamp. Reject files with >5% missing values.
- **Storage:** Upload → FastAPI validates → writes to QuestDB (for querying) and exports to Parquet (for `ParquetDataCatalog`).
- **UI:** Simple file upload button in the Tauri app. Drag-and-drop is a nice-to-have for post-MVP.
- **Endpoint:** `POST /api/data/upload` with `multipart/form-data`.

### 10.2 Authentication model — 🟡

**Recommendation: No authentication for the local app MVP.**

The `USERS` table and JWT auth are for the future platform app. A single-user desktop app running on `localhost` does not need auth. Other processes on the same machine *can* access the API — this is acceptable for a personal research tool.

For MVP, hardcode a single default user ID in the database. Add auth when the platform app is built.

### 10.3 Error handling / retry strategy — 🟡

**Recommendation:**

| Failure Type | Strategy |
|---|---|
| Data provider connection failure | Exponential backoff (1s, 2s, 4s, 8s, max 60s) with circuit breaker (5 failures → 5min cooldown). Log and surface in UI. |
| Backtest failure | Do not auto-retry. Report error to user via WebSocket with stack trace. User decides whether to fix and re-run. |
| QuestDB write failure | Retry 3x with 1s delay. On persistent failure, log the dropped data and alert the user. Do not buffer in Redis (adds complexity for a rare failure mode). |
| Frontend WebSocket disconnect | Auto-reconnect with exponential backoff (1s, 2s, 4s, max 30s). Show "Reconnecting..." banner in UI. On reconnect, re-fetch current state via REST. |

---

## 11. BacktestEngine vs BacktestNode

### 11.1 API assignment — 🔴

**Recommendation: `BacktestNode` in Celery workers for all backtests. `BacktestEngine` is not used in production — it's a reference for understanding the low-level API.**

Rationale:
- `BacktestNode` with `BacktestRunConfig` is NautilusTrader's recommended production API.
- `task_queue.md` and `core_engine.md` Celery examples both use `BacktestNode`.
- `BacktestEngine.reset()` could theoretically reuse engines within prefork workers, but `worker_max_tasks_per_child=50` means workers restart frequently anyway. The complexity of engine reuse is not worth the marginal benefit.
- Update `system_design.md`'s class diagram to show `BacktestNode` instead of `BacktestEngine` in `NautilusBacktestService`.

For strategy validation (dry-run), do **not** use `BacktestEngine` in FastAPI. Instead, validate in a short-lived Celery task that imports the user's strategy class and checks for required method signatures (`on_bar`, `on_start`, etc.) without running a simulation.

---

## 12. Data Pipeline: QuestDB → Parquet → ParquetDataCatalog

### 12.1 Export mechanism — 🔴

**Recommendation: On-demand export triggered before each backtest, not periodic or dual-write.**

Flow:
1. User submits a backtest request with symbols and date range.
2. FastAPI enqueues a Celery chain: `export_to_parquet.s(symbols, start, end) | run_backtest.s(strategy_id, config)`.
3. The `export_to_parquet` task queries QuestDB via PGWire, converts to Parquet using PyArrow, and writes to the shared Parquet catalog directory.
4. The `run_backtest` task reads from the Parquet catalog via `ParquetDataCatalog`.

This ensures data is always fresh when a backtest starts, avoids stale Parquet files, and eliminates the need for Celery Beat scheduling or dual-write complexity. The export is fast (~seconds for typical date ranges) because QuestDB's columnar storage streams efficiently.

### 12.2 Parquet catalog Docker volume — 🟠

**Recommendation: Define a named Docker volume shared between the data export task and Celery workers.**

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

Both the FastAPI service (which triggers exports via Celery) and Celery workers (which read from the catalog) mount the same volume at `/data/validated`. This maps directly to the `ParquetDataCatalog(path="/data/validated")` shown in `data_providers.md`.

---

## 13. Parameter Sweep Scalability

### 13.1 Sweep duration — 🟡

**Recommendation: Accept ~10 min for a 500-combination sweep. Provide UI progress.**

With 4 workers and ~5s per backtest: 500 / 4 = 125 batches × 5s = ~10 minutes. This is acceptable for research iteration. Faster sweeps can be achieved by increasing `worker_concurrency` on machines with more cores.

UI progress: The sweep Celery group should publish aggregate progress to a Redis channel (`sweep:{id}:progress`), and FastAPI should forward it via WebSocket: `"234/500 complete, ~4 min remaining"`. Use Celery's `GroupResult.completed_count()` to track.

### 13.2 Memory pressure — 🟡

**Recommendation: NautilusTrader uses memory-mapped Parquet files via Arrow, so memory overhead is manageable.**

`ParquetDataCatalog` reads Parquet files via PyArrow, which supports memory-mapped I/O. Multiple workers reading the same Parquet files on the shared volume will share OS-level page cache, not duplicate data in each process's heap.

For a 500-symbol × 20-year daily OHLCV dataset (~36M rows × ~48 bytes/row ≈ 1.7 GB on disk), expect ~200–500 MB per worker in practice (Arrow memory-maps lazily). With `worker_max_tasks_per_child=50`, workers restart and release memory periodically.

---

## 14. Strategy Code: Authoring, Validation, and Execution

### 14.1 Strategy template system — 🟠

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

### 14.2 Strategy dry-run validation — 🟠

**Recommendation: Validation = Python import + introspection in a short-lived subprocess, not a BacktestEngine dry run.**

Steps:
1. FastAPI receives `POST /api/strategies/validate` with Python source code.
2. FastAPI enqueues a Celery task `validate_strategy(code)`.
3. The Celery worker writes the code to a temp file, imports it in a subprocess with `importlib`, and checks:
   - Class inherits from `TradingStrategy`
   - Required methods exist (`on_start`, `on_bar` or `on_quote_tick`)
   - No syntax errors (already caught by Pyodide client-side, but double-checked)
4. Returns success/error to FastAPI, which forwards to the frontend.

This avoids executing arbitrary code in the FastAPI process and avoids instantiating a `BacktestEngine` for validation. The subprocess provides basic isolation.

### 14.3 Strategy code serialization — 🔴

**Recommendation: Store Python source in PostgreSQL (`STRATEGIES.python_code` column). Pass the strategy ID (not source code) to Celery. The worker loads the code from the database and `exec()`s it.**

Flow:
1. Monaco editor → `POST /api/strategies` → FastAPI stores source in PostgreSQL → returns `strategy_id`.
2. `POST /api/backtest/run` → FastAPI enqueues `run_backtest.s(strategy_id, config)` (JSON-serializable).
3. Celery worker receives `strategy_id`, queries PostgreSQL for the Python source, `exec()`s it to define the class, and registers it with `BacktestNode`.

```python
@shared_task
def run_backtest(strategy_id: str, config: dict):
    code = db.fetch_strategy_code(strategy_id)  # From PostgreSQL
    namespace = {}
    exec(code, namespace)
    strategy_cls = namespace["MyStrategy"]  # Convention: class must be named MyStrategy
    node = BacktestNode(configs=build_config(strategy_cls, config))
    node.run()
    return node.get_results()
```

This keeps Celery messages small (just IDs) and avoids passing Python source through the message queue.

---

## 15. NautilusTrader Data Types vs Provider Data

### 15.1 Data type conversion stage — 🟠

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

## 16. Granian vs Uvicorn

### 16.1 Contradiction resolution — 🟠

**Recommendation: Uvicorn is the canonical ASGI server. Update `python_rust_or_go.md` to remove the Granian recommendation.**

`asgi_web_server.md` provides a thorough benchmark-driven analysis concluding that Uvicorn wins for QuantLens's database-heavy workload. The Granian recommendation in `python_rust_or_go.md` was written before this analysis. Update the "Performance Optimization Strategy" section in `python_rust_or_go.md`:

Change:
> Use Granian (Rust-based ASGI server) for HTTP handling.

To:
> Use Uvicorn with uvloop + httptools for HTTP handling. See [asgi_web_server.md](asgi_web_server.md) for the benchmark-driven rationale — Uvicorn outperforms Granian on database-heavy workloads.

---

## 17. skfolio Integration Boundary

### 17.1 NautilusTrader → skfolio handoff — 🟠

**Recommendation: A Celery task converts NautilusTrader results to asset-return DataFrames for skfolio. The conversion is strategy-level, not asset-level.**

Flow:
1. User runs N backtests (one per strategy or parameter set), each producing an equity curve.
2. User requests portfolio optimization via `POST /api/optimize`.
3. FastAPI retrieves equity curves from PostgreSQL (`RESULTS.equity_curve`).
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

This runs in the FastAPI process (via `ProcessPoolExecutor`) since it's a quick computation, or as a Celery task for large portfolios.

---

## 18. Live/Paper Trading Path

### 18.1 Scope — 🟢

**Recommendation: Live and paper trading are explicitly future scope. Label them as such in `core_engine.md`.**

For MVP, QuantLens is a **backtest-only** tool. The backtest-live parity claim in `core_engine.md` is a value proposition for the platform — it means users won't need to rewrite strategies when live trading is eventually added.

Add a note to `core_engine.md`:
> **MVP Scope:** QuantLens v1 supports backtesting only. Live and paper trading via broker adapters (Binance, Interactive Brokers, etc.) are planned for a future release. The architecture is designed so that strategies written for backtesting will run in live mode with zero code changes when this feature is added.

The Tier 2 real-time gateway in `asgi_web_server.md` is the foundation for this future work and should be labeled accordingly.
