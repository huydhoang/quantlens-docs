# Integration Questions

Deep-dive cross-referencing of [ARCHITECTURE.md](ARCHITECTURE.md), [local_frontend.md](local_frontend.md), [backend_server.md](backend_server.md), and [core_engine.md](core_engine.md) against all other architecture documents surfaced the following integration questions, contradictions, and unresolved design decisions.

---

## Cross-Review: ARCHITECTURE.md × local_frontend.md × backend_server.md

---

## 1. Frontend ↔ API Layer Communication

### 1.1 Who owns the API process — Tauri or Docker?

`ARCHITECTURE.md` shows FastAPI inside the "Docker Compose (Local)" subgraph (Deployment Architecture diagram), implying it runs as a Docker container. But `local_frontend.md` shows a direct `Tauri (Rust Core) → FastAPI + Uvicorn (Localhost)` connection, which reads like FastAPI runs as a bare localhost process outside Docker.

**Question:** Does FastAPI run inside a Docker container (alongside PostgreSQL, QuestDB, Redis, etc.) or as a native process managed by Tauri's Rust backend? This affects:
- Port binding and networking (container-to-container vs host-to-host)
- Startup orchestration (does `docker compose up` start FastAPI, or does Tauri launch it?)
- Development workflow (hot-reload of FastAPI inside Docker vs native `uvicorn --reload`)

### 1.2 How does the Tauri app discover the FastAPI backend?

`local_frontend.md` hardcodes `ws://localhost:8000/ws/backtest` and `http://localhost:3000` (React dev server in CORS config). `backend_server.md` adds a second service on port 8001 for the real-time gateway.

**Question:** What's the service discovery mechanism?
- Are ports hardcoded in the frontend, or does Tauri's Rust backend provide them via IPC?
- If FastAPI is in Docker, does the container expose ports to the host, or does Tauri communicate via Docker networking?
- In the two-tier architecture (port 8000 + 8001), how does the React SPA know which WebSocket endpoint to connect to for backtest progress (Tier 1) vs market data (Tier 2)?

### 1.3 CORS configuration contradiction

`backend_server.md` sets `allow_origins=["http://localhost:3000"]` (Vite dev server). But in production, the Tauri webview loads from a `tauri://` or `https://tauri.localhost` origin, not `http://localhost:3000`.

**Question:** What's the CORS strategy for the production Tauri build? Options:
- Tauri's Rust backend proxies all API calls (no CORS needed)
- FastAPI allows the Tauri-specific origin
- CORS is disabled entirely since both run locally on the same machine

---

## 2. Backtest Execution: FastAPI ↔ Celery ↔ NautilusTrader

### 2.1 Contradictory communication paths between frontend and backtest engine

`ARCHITECTURE.md` (Local App diagram) shows: `Frontend → HTTP/WebSocket → API → Celery/Redis → Nautilus`. But the Deployment Architecture diagram in the same file shows: `Tauri Desktop App → Enqueue Jobs → Redis` (direct, bypassing FastAPI).

**Question:** Does the Tauri frontend enqueue Celery jobs directly to Redis, or does it go through FastAPI? Direct Redis access from the frontend:
- Bypasses validation, auth, and rate limiting
- Requires the Tauri Rust backend to speak the Celery protocol
- Contradicts the FastAPI-as-proxy pattern shown in the Backtest Execution Flow sequence diagram

### 2.2 WebSocket progress broadcasting — who pushes to the client?

The Backtest Execution Flow in `ARCHITECTURE.md` shows: `Worker → Queue → API → UI (WebSocket)`. But `backend_server.md` (Tier 2) shows a Redis pub/sub pattern where the vanilla ASGI gateway subscribes to Redis channels and forwards to WebSocket clients.

**Question:** Which service owns the backtest progress WebSocket?
- **Tier 1 (FastAPI):** As shown in `ARCHITECTURE.md` — FastAPI manages WebSocket connections and receives progress from Celery/Redis
- **Tier 2 (Vanilla ASGI):** As shown in `backend_server.md` — a separate process on port 8001 handles all WebSocket streaming

If it's Tier 1, then backtest progress and market data WebSockets live on different services (FastAPI vs vanilla ASGI). How does the frontend manage two separate WebSocket connections to two different ports?

### 2.3 NautilusTrader lifespan management in FastAPI

`backend_server.md` Tier 1 implementation shows a `NautilusKernel` initialized in FastAPI's lifespan context:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.kernel = NautilusKernel()
    await app.state.kernel.start()
    yield
    await app.state.kernel.stop()
```

But `core_engine.md` and `ARCHITECTURE.md` both state that NautilusTrader runs **in Celery workers**, not in the FastAPI process. The ARCHITECTURE.md explicitly says: "NautilusTrader is a **library, not a service** — the API layer enqueues jobs to Celery; workers import and call `nautilus_trader` directly in-process."

**Question:** Is `NautilusKernel` in FastAPI's lifespan a mistake, or is it intentional for a different purpose (e.g., strategy validation, data catalog access)? If backtests run exclusively in Celery workers, what does the FastAPI-hosted kernel do?

### 2.4 ProcessPoolExecutor vs Celery for CPU-bound work

`backend_server.md` shows:

```python
executor = ProcessPoolExecutor(max_workers=4)

@app.post("/optimize-portfolio")
async def optimize_portfolio(holdings: dict):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, run_optimization_sync, holdings)
```

Meanwhile, `task_queue.md` shows Celery handling all background work including optimization.

**Question:** Which CPU-bound tasks use `ProcessPoolExecutor` (in-process) vs Celery (distributed)?
- Are skfolio optimizations always synchronous (ProcessPoolExecutor) while backtests are always async (Celery)?
- If both are used, what's the decision boundary? Latency tolerance? Expected runtime?
- Does the `ProcessPoolExecutor` conflict with Uvicorn's `--workers 4` flag (both forking processes)?

---

## 3. Two-Tier Architecture: When and How

### 3.1 Is the two-tier architecture for MVP or future?

`backend_server.md` presents the hybrid two-tier setup (FastAPI on 8000 + Vanilla ASGI on 8001) as the recommended production architecture. But the final verdict says: "start with **FastAPI on Uvicorn**. When live trading is added, extract real-time endpoints."

The ARCHITECTURE.md and local_frontend.md show only a single API layer (FastAPI).

**Question:** Is the local desktop app (MVP) single-tier or two-tier?
- If single-tier, should `backend_server.md`'s Tier 2 code be labeled as "future" to avoid confusion?
- If two-tier from day one, the Docker Compose config, frontend WebSocket management, and CORS setup all need to account for two backend services

### 3.2 NautilusKernel shared between tiers

The two-tier diagram in `backend_server.md` shows both tiers connecting to a "Shared Layer" containing `NautilusTrader kernel`. But NautilusTrader enforces a **one-BacktestNode-per-process** constraint (documented in `ARCHITECTURE.md` and `core_engine.md`).

**Question:** How do two separate Uvicorn processes (Tier 1 + Tier 2) share a NautilusTrader kernel?
- Is the "shared" kernel a misconception? Each tier would need its own kernel instance
- Or is the kernel shared via Redis/IPC rather than in-process?
- Does Tier 2's `check_signal` function (which calls `self.nautilus.process_tick()`) require a full `BacktestNode`, or is it using a lighter-weight NautilusTrader component?

---

## 4. Data Layer Contradictions

### 4.1 QuestDB vs TimescaleDB — which is the local default?

**RESOLVED:** QuestDB is the confirmed database for OHLCV data in Docker Compose.

After comprehensive benchmarking (see `ohlcv_database.md`), QuestDB was selected because:
- **1.7x faster data ingestion** (332K vs 194K rows/sec)
- **4.7x faster aggregations** across all symbols (critical for multi-asset backtesting)
- **Purpose-built for financial markets** with native `SAMPLE BY`, `ASOF JOIN`, and `LATEST ON`
- **Local Docker deployment** eliminates the free-tier constraints that previously favored TimescaleDB

The `psycopg2` compatibility issues (no scrollable cursors) are not a concern because the stack uses `asyncpg` for all PostgreSQL wire protocol connections, which works correctly with QuestDB.

### 4.2 QuestDB access protocol inconsistency

`backend_server.md` shows two different QuestDB access patterns:
- **Writes:** HTTP REST (Influx Line Protocol) via `session.post("http://localhost:9000/write", data=line)`
- **Reads:** PGWire protocol via `asyncpg.create_pool(dsn="postgresql://localhost:8812/qdb")`
- **Tier 2 writes:** Also PGWire via `pool.execute("INSERT INTO ohlcv_1m ...")`

**Question:** Which write protocol is canonical for QuestDB in QuantLens?
- ILP over HTTP (port 9000) — optimized for high-throughput ingestion
- ILP over TCP (port 9009) — even higher throughput, documented in ARCHITECTURE.md
- PGWire SQL INSERT (port 8812) — shown in Tier 2 code
- Are different protocols used for different tiers (ILP for bulk ingestion, PGWire for individual tick writes)?

### 4.3 MongoDB → DuckDB (RESOLVED)

**RESOLVED:** MongoDB has been replaced by **DuckDB** (embedded, in-process) for fundamentals and economic indicators.

MongoDB's official Docker image and the community server image both encountered persistent **connection errors** during local benchmarking — a common issue with Docker-containerized databases for desktop app use cases. DuckDB eliminates this class of problems entirely by running embedded in the Python process with zero configuration. See [fundamentals_database.md](fundamentals_database.md) for the full rationale.

The deployment architecture diagram in `ARCHITECTURE.md` has been updated to show DuckDB as an embedded database alongside LanceDB, separate from the Docker Compose services.

### 4.4 PostgreSQL — single instance or separate per concern?

`ARCHITECTURE.md` shows a single PostgreSQL instance for Strategies, Backtest Results, and User Data. But Celery also uses Redis (not PostgreSQL) as its result backend (`task_queue.md`). Meanwhile, `backend_server.md` references `asyncpg` connections to both PostgreSQL and QuestDB (PGWire).

**Question:** How many PostgreSQL-compatible connections does the FastAPI app maintain?
- One `asyncpg` pool for PostgreSQL (strategies, results, users)
- One `asyncpg` pool for QuestDB (OHLCV via PGWire on port 8812)
- Are these pools configured separately, or does a connection manager abstract them?

---

## 5. Real-Time Data Flow

### 5.1 WebSocket fan-in/fan-out architecture

`backend_server.md` Tier 2 shows individual WebSocket connections to Finnhub and Alpaca, with data published to Redis channels. But `ARCHITECTURE.md`'s Data Flow Architecture shows a separate "Data Ingestion Service" with a "Data Normalizer" component.

**Question:** Is the data ingestion service the same as the Tier 2 vanilla ASGI gateway, or is it a separate process?
- If they're the same, the Tier 2 gateway handles both ingestion (Finnhub/Alpaca → QuestDB) and serving (QuestDB → React frontend)
- If separate, where does the ingestion service run? Another Docker container? A Celery worker?

### 5.2 Finnhub WebSocket data type mismatch

`backend_server.md` Tier 2 code subscribes to `"BINANCE:BTCUSDT"` on Finnhub's WebSocket, which is a crypto trade stream. But `data_providers.md` says Finnhub **Stock Candles (OHLCV) and Tick Data are Premium-only** on the free tier, and the free WebSocket provides real-time **trade streaming** (not OHLCV bars).

**Question:** The Tier 2 code inserts into `ohlcv_1m` table, but the raw Finnhub WebSocket data is individual trades, not OHLCV bars.
- Is the OHLCV bar generation happening in QuestDB (via `SAMPLE BY`) or in the Python ingestion layer?
- If in QuestDB, the `INSERT INTO ohlcv_1m` statement should be inserting into a `trades` table, not `ohlcv_1m`
- What does the schema look like for raw trade ingestion vs aggregated bars?

### 5.3 Market data for the React frontend — REST or WebSocket?

`ARCHITECTURE.md` Data Flow Architecture shows: `Tiingo/Finnhub/Alpaca → FastAPI WebSocket → Market Data Hook → Price Ticker Component`. But `local_frontend.md` shows TanStack Query handling REST data, with WebSocket pushing into TanStack Query cache.

**Question:** Does the React frontend get live market data via:
- A dedicated WebSocket connection (as shown in ARCHITECTURE.md)?
- REST polling with TanStack Query's `refetchInterval`?
- WebSocket updates pushed into TanStack Query cache (as shown in local_frontend.md)?
- All three, depending on the data type?

---

## 6. Tauri-Specific Integration

### 6.1 Tauri's Rust backend — used or unused?

`local_frontend.md` positions Tauri as a lightweight shell wrapping a WebView. But Tauri v2's Rust backend can handle IPC commands, file system access, and even HTTP requests via its plugin system.

**Question:** Does QuantLens use any Tauri Rust commands (`#[tauri::command]`)?
- File operations (loading/saving strategy files locally)?
- System monitoring (CPU/memory usage of backtest workers)?
- Native notifications (backtest complete)?
- Or is Tauri purely a WebView container with all logic in the React SPA + FastAPI?

### 6.2 Tauri + Docker Compose startup orchestration

The user story says `docker compose up` starts all backend services. But Tauri is a native desktop app, not a Docker container.

**Question:** What's the startup sequence?
1. User runs `docker compose up` manually, then opens the Tauri app?
2. Tauri's Rust backend runs `docker compose up` on launch?
3. A launcher script starts both Docker Compose and the Tauri app?
- What happens if Docker services aren't running when the Tauri app opens? Is there a health check / connection retry UI?

---

## 7. Strategy Execution Security

### 7.1 Where is Python strategy code executed?

`ARCHITECTURE.md` mentions "Sandbox Python execution (restricted environment, no network access)" under Security Considerations. The Monaco Editor flow shows strategy code sent to FastAPI for validation, then to NautilusTrader for execution.

**Question:** What sandboxing mechanism is used, and at which layer?
- `todos.md` lists this as "Not Started" — is there an interim plan for MVP?
- Does the Celery worker run user code in an unrestricted Python process?
- If strategies run inside Docker containers (Celery workers), does Docker provide sufficient isolation, or is additional sandboxing (RestrictedPython, nsjail, Pyodide server-side) needed?
- Is the threat model "malicious user code" (multi-tenant) or "accidental harmful code" (single-user local app)?

For a local-first single-user app, the threat model is arguably just accidental harm (infinite loops, excessive memory). Full sandboxing may be overengineered for MVP.

---

## 8. Platform App Integration (Future)

### 8.1 What data flows from local app to platform?

`ARCHITECTURE.md` shows: `QuantLens Local App → Submit Results · Deploy Strategy → TanStack Start + React → Neon PostgreSQL`. But there's no specification of what "submit results" means.

**Question:**
- Does the local app upload raw backtest results (trades, equity curves, metrics)?
- Does it upload the strategy code itself?
- Is there an API contract between the local app and the platform app?
- Authentication: How does the local app authenticate with the deployed platform?

---

## 9. Data Provider Contradictions

### 9.1 Tiingo rate limits — inconsistent across docs

`ARCHITECTURE.md` says "Tiingo limits are plan-dependent (hourly requests + daily requests + monthly bandwidth — see pricing page)." `data_providers.md` lists "50 requests/hour · 1,000 requests/day · 500 unique symbols/month." The `todos.md` completed item says "'50 req/hr' was incorrect."

**Question:** If `50 req/hr` is incorrect per todos.md, what's the actual Tiingo free-tier rate limit? And should `data_providers.md`'s table be corrected?

---

## 10. Missing Specifications

### 10.1 Custom dataset upload pipeline

`user_stories.md` includes "bring-your-own data (custom datasets I upload via the app UI)." `todos.md` lists this as "Not Started." Neither `ARCHITECTURE.md` nor `backend_server.md` specifies the upload flow.

**Question:** What's the planned pipeline?
- File format support (CSV, Parquet, Excel)?
- Validation rules (required columns, timestamp format, data quality checks)?
- Storage destination (direct to ParquetDataCatalog, or QuestDB first)?
- UI component (drag-and-drop in the Tauri app)?

### 10.2 Authentication and authorization

`ARCHITECTURE.md` database schema includes a `USERS` table. `backend_server.md`'s two-tier diagram mentions "JWT auth" for Tier 1. But for a local-first single-user desktop app:

**Question:** Is authentication needed for the local app?
- If single-user, why is there a USERS table?
- Is the USERS table only for the future platform app?
- Does JWT auth protect the local FastAPI endpoints, or is it only for the deployed platform?
- If no auth locally, what prevents other processes on the same machine from accessing the API?

### 10.3 Error handling and retry strategy

`backend_server.md` Tier 2 shows a bare `except Exception` with a 5-second reconnect delay for Finnhub WebSocket failures. `task_queue.md` shows Celery retry with `max_retries=2` and `countdown=30`.

**Question:** What's the unified error handling strategy?
- Data provider connection failures: exponential backoff? circuit breaker?
- Backtest failures: retry automatically or report to user?
- QuestDB write failures: buffer in Redis and retry, or drop?
- WebSocket disconnections from the React frontend: auto-reconnect with what backoff?

---

## Cross-Review: core_engine.md

---

## 11. BacktestEngine vs BacktestNode — Which API for Which Workflow?

### 11.1 Conflicting API recommendations across docs

`core_engine.md` presents two NautilusTrader APIs for different workflows:
- **`BacktestEngine`** (low-level) — for rapid research iteration with `engine.reset()` for tight loops without process restart
- **`BacktestNode`** (high-level) — for production backtests via Celery workers, one per process

But `ARCHITECTURE.md` Section 2 ("NautilusTrader Integration") only mentions `BacktestEngine` in the class diagram (`NautilusBacktestService` has a `+BacktestEngine engine` field), while the Key Implementation Recommendations say: "Use `BacktestEngine` (low-level, fine-grained control) **or** `BacktestNode` with `BacktestRunConfig` objects (high-level, recommended for production)."

Meanwhile, `task_queue.md` exclusively uses `BacktestNode` in its Celery integration example:

```python
@shared_task(bind=True, max_retries=2)
def run_nautilus_backtest(self, data, strategy_id, config):
    node = BacktestNode(configs=config)
    node.run()
    return node.get_results()
```

And `core_engine.md`'s own Celery example uses `BacktestNode`:

```python
job = group(run_backtest.s(strategy_id, params) for params in param_grid)
```

**Question:** Which API is used where?
- Is `BacktestEngine` used in FastAPI's process for quick "validate strategy" dry runs, while `BacktestNode` is used in Celery workers for full backtests?
- The `ARCHITECTURE.md` class diagram shows `BacktestEngine` in `NautilusBacktestService` — is this service instantiated in FastAPI or in Celery workers?
- Can `BacktestEngine.reset()` be used inside a Celery prefork worker to reuse the engine across multiple tasks, or does `worker_max_tasks_per_child=50` (from `task_queue.md`) mean each worker gets a fresh `BacktestNode` per task?

---

## 12. Data Pipeline: QuestDB → Parquet → ParquetDataCatalog

### 12.1 Export mechanism undefined

`core_engine.md` states: "One data pipeline (QuestDB → Parquet → `ParquetDataCatalog`)" and the integration table says: "Historical OHLCV stored in QuestDB (local Docker), exported to Parquet for NautilusTrader's native data catalog."

But no document specifies **how** data moves from QuestDB to Parquet files:

**Question:** What is the QuestDB → Parquet export mechanism?
- QuestDB supports native Parquet export via `COPY` SQL command — is this used?
- Is there a scheduled Celery Beat task that periodically exports from QuestDB to Parquet?
- Or does a data ingestion service write to both QuestDB and Parquet simultaneously (dual-write)?
- If the export is periodic, how does the system ensure Parquet files are up-to-date when a backtest starts? Does the backtest task trigger an export before running?

### 12.2 Parquet catalog path and Docker volume mapping

`data_providers.md` shows `ParquetDataCatalog(path="/data/validated")` as the catalog path. But NautilusTrader runs inside Celery workers, which run in Docker containers.

**Question:** How does the Parquet catalog path map to Docker volumes?
- Is `/data/validated` a Docker volume shared between the data ingestion service and Celery workers?
- If QuestDB runs in one container and Celery workers in another, how does the export + catalog read work across containers?
- Does `docker compose` define a shared volume for the Parquet catalog?

---

## 13. Parameter Sweep Scalability

### 13.1 Celery worker count vs parameter grid size

`core_engine.md` claims: "QuantLens's target users run hundreds to low thousands of combinations — well within Celery worker parallelism." The Celery config in `task_queue.md` sets `worker_concurrency=4`.

**Question:** With 4 concurrent workers and "hundreds to low thousands" of parameter combinations, what's the expected sweep duration?
- A 500-combination sweep with 4 workers = 125 sequential batches. If each backtest takes 5 seconds, that's ~10 minutes. Is this acceptable for research iteration?
- Does the system provide sweep progress in the UI (e.g., "234/500 complete, ~4 min remaining")?
- Is there a UI for configuring the parameter grid, or does the user write the grid in Python code in the Monaco editor?

### 13.2 Memory pressure from parallel BacktestNodes

`core_engine.md` notes VectorBT's limitation: "Large parameter grids over many assets can exceed available RAM." NautilusTrader's BacktestNode loads the `ParquetDataCatalog` per process.

**Question:** With 4 Celery prefork workers, each loading a full `ParquetDataCatalog` for the same universe of symbols:
- Does each worker load a separate copy of the data into memory, or does NautilusTrader use memory-mapped files?
- For a 500-symbol × 20-year daily OHLCV catalog (~36M rows), what's the per-worker memory footprint?
- `task_queue.md` sets `worker_max_tasks_per_child=50` to mitigate memory leaks — does this force a full data reload every 50 tasks?

---

## 14. Strategy Code: Authoring, Validation, and Execution

### 14.1 Strategy template system undefined

`core_engine.md` states: "QuantLens provides strategy templates and Monaco editor autocompletion to reduce NautilusTrader's boilerplate." The integration table says: "Strategy templates target NautilusTrader's `TradingStrategy` API exclusively — one template system, one validation path."

But no document defines the template system:

**Question:** What does the strategy template system look like?
- Are templates pre-built `.py` files served by FastAPI (`GET /api/strategies/template`)?
- Do templates include the full NautilusTrader boilerplate (`Strategy` base class, `on_start`, `on_bar`, `on_stop` methods), leaving the user to fill in logic?
- Are there multiple templates (SMA crossover, momentum, mean reversion) or a single generic template?
- How does the Monaco editor provide NautilusTrader-specific autocompletion? `ARCHITECTURE.md` mentions a custom `CompletionItemProvider` but doesn't specify what completions are offered.

### 14.2 Strategy validation — dry run vs AST parse

`core_engine.md` says strategies are validated before execution. `ARCHITECTURE.md`'s Monaco Editor flow shows two validation stages:
1. Client-side: Pyodide WASM AST parsing for syntax errors
2. Server-side: `POST /api/strategies/validate → FastAPI → Nautilus: Dry-run parse`

**Question:** What does "dry-run parse" mean for NautilusTrader?
- Does NautilusTrader have a built-in strategy validation API, or does QuantLens implement this by importing the user's class and checking for required method signatures?
- Does the dry-run actually instantiate a `BacktestEngine` with no data, or is it purely a Python import + introspection?
- If the dry-run imports user code, this executes arbitrary Python in the FastAPI process. Does this conflict with the sandboxing concern (question 7.1)?

### 14.3 Strategy code serialization for Celery

`core_engine.md` shows strategies executed in Celery workers. `task_queue.md` configures `task_serializer="json"` and `accept_content=["json"]`. But a strategy is Python source code (a class definition), not a JSON-serializable object.

**Question:** How does strategy code get from the Monaco editor to a Celery worker?
- Is the Python source stored in PostgreSQL (`STRATEGIES.python_code` column in `ARCHITECTURE.md` schema), and the worker loads it by ID from the database?
- Or is the source code passed as a JSON string in the Celery task arguments?
- If loaded from the database, the worker must `exec()` or `importlib` the code at runtime — how does this interact with NautilusTrader's strategy registration?

---

## 15. NautilusTrader Data Types vs Provider Data

### 15.1 Adapter pattern — who converts, when?

`core_engine.md` integration table: "Single adapter pattern normalizing Tiingo/Alpaca/Finnhub data to NautilusTrader `Bar`/`QuoteTick` types."

But the data flow in `ARCHITECTURE.md` and `data_providers.md` shows data going through multiple stages: Provider → Validation → QuestDB → Parquet → ParquetDataCatalog.

**Question:** At which stage does the conversion to NautilusTrader types happen?
- During ingestion (provider response → NautilusTrader `Bar` objects → Parquet)?
- During catalog read (Parquet files → NautilusTrader `Bar` objects inside `ParquetDataCatalog`)?
- If conversion happens at ingestion time, the QuestDB and Parquet stores contain NautilusTrader-formatted data. Does this lock the storage schema to NautilusTrader's format?
- If conversion happens at read time, the Parquet files store a QuantLens-native schema and the catalog adapter converts on-the-fly. Is this the intended design?

---

## 16. Granian vs Uvicorn Contradiction — ✅ Resolved

### 16.1 Resolution: Gunicorn+Uvicorn Raw ASGI is the canonical default

Extended benchmarks on QuantLens's actual CPU-bound workload (skfolio portfolio optimization) confirm that **Gunicorn+Uvicorn Raw ASGI** is the default server stack. `python_rust_or_go.md` has been updated to remove the Granian recommendation. `backend_server.md` now documents the extended benchmark results and the final decision: Gunicorn+Uvicorn Raw ASGI by default; FastAPI on Gunicorn+Uvicorn only when WebSocket support is explicitly required.

---

## 17. skfolio Integration Boundary

### 17.1 Results handoff between NautilusTrader and skfolio

`core_engine.md` System Flow diagram shows: `Validation → Portfolio Layer → Strategy Returns → skfolio Optimization → Allocation Weights`. The integration table says: "Portfolio optimization runs independently of the backtest engine — NautilusTrader produces trade results, skfolio optimizes allocations."

But no document specifies the data contract between NautilusTrader results and skfolio inputs.

**Question:** What is the NautilusTrader → skfolio handoff format?
- skfolio's `MeanRisk` optimizer expects a DataFrame of asset returns (rows = time observations, columns = assets). NautilusTrader produces per-strategy trade results and equity curves.
- Who converts NautilusTrader's trade-level results into asset-level return series for skfolio?
- If a user runs 5 strategies on different asset universes, does skfolio optimize across all 5 strategies (strategy-level allocation) or across underlying assets (asset-level allocation)?
- Is the conversion logic in the FastAPI backend, in a Celery task, or in the React frontend?

---

## 18. Live/Paper Trading Path

### 18.1 Backtest-live parity claim vs current architecture

`core_engine.md`'s key selling point: "Backtest-live parity — identical strategy code runs in backtest and live modes with zero changes." The decision rationale states this is "the most important factor for a tool targeting serious quants."

But no other document describes the live trading architecture:

**Question:** How does the live trading path work in QuantLens?
- `core_engine.md` mentions broker adapters (Binance, Interactive Brokers, OKX, Bybit) — are any of these configured in QuantLens, or is live trading entirely future scope?
- `data_providers.md` says Alpaca is for "paper trading only." Does QuantLens have a paper trading mode, or is this deferred?
- If live/paper trading is future scope, should `core_engine.md` explicitly label it as such to avoid setting incorrect expectations about MVP capabilities?
- The `backend_server.md` Tier 2 real-time gateway assumes live signal processing with `nautilus.process_tick()`. Is this the live trading path, and if so, how does it relate to the Tier 1 backtest workflow?
