# Integration Questions

Deep-dive cross-referencing of [ARCHITECTURE.md](ARCHITECTURE.md), [local_frontend.md](local_frontend.md), [backend_server.md](backend_server.md), and [core_engine.md](core_engine.md) against all other architecture documents surfaced the following integration questions, contradictions, and unresolved design decisions.

---

## Backend

### Backtest Execution: API Layer ↔ Huey ↔ NautilusTrader

#### 2.1 Contradictory communication paths between frontend and backtest engine

`ARCHITECTURE.md` (Local App diagram) shows: `Frontend → HTTP/WebSocket → API → Huey/Redis → Nautilus`. But the Deployment Architecture diagram in the same file shows: `Tauri Desktop App → Enqueue Jobs → Redis` (direct, bypassing the API layer).

**Question:** Does the Tauri frontend enqueue Huey tasks directly to Redis, or does it go through the Raw ASGI API? Direct Redis access from the frontend:
- Bypasses validation, auth, and rate limiting
- Requires the Tauri Rust backend to speak the Huey protocol
- Contradicts the API-as-proxy pattern shown in the Backtest Execution Flow sequence diagram

#### 2.2 WebSocket progress broadcasting — who pushes to the client?

The Backtest Execution Flow in `ARCHITECTURE.md` shows: `Worker → Queue → API → UI (WebSocket)`. With Gunicorn+Uvicorn Raw ASGI as the unified backend, the pattern would be Redis pub/sub where the Raw ASGI API subscribes to Redis channels and forwards to WebSocket clients.

**Question:** Specifically, how does the Raw ASGI API manage the WebSocket lifecycle for backtest progress?
- Does a long-lived Redis subscription run per connected client, or does the Raw ASGI handler poll a Redis key?
- How does the API route progress messages from a Huey worker back to the specific WebSocket client that initiated the backtest?

#### 2.3 NautilusTrader lifespan management in the API process

Some earlier API examples show a `NautilusKernel` initialized in the application's lifespan context. But `core_engine.md` and `ARCHITECTURE.md` both state that NautilusTrader runs **in Huey workers**, not in the API process. `ARCHITECTURE.md` explicitly says: "NautilusTrader is a **library, not a service** — the API layer enqueues jobs to Huey; workers import and call `nautilus_trader` directly in-process."

**Question:** Should any NautilusTrader component be initialized in the Raw ASGI API lifespan?
- If backtests run exclusively in Huey workers, what purpose does an API-hosted kernel serve?
- Is it needed for strategy validation (dry-run) or data catalog access, or should validation also run in a Huey task?

#### 2.4 ProcessPoolExecutor vs Huey for CPU-bound work

`backend_server.md` shows skfolio portfolio optimization dispatched via `ProcessPoolExecutor` in the Raw ASGI handler. Meanwhile, `task_queue.md` shows Huey handling all background work including optimization.

**Question:** Which CPU-bound tasks use `ProcessPoolExecutor` (in-process) vs Huey (distributed)?
- Are skfolio optimizations always synchronous (ProcessPoolExecutor) while backtests are always async (Huey)?
- If both are used, what's the decision boundary? Latency tolerance? Expected runtime?
- Does the `ProcessPoolExecutor` conflict with Gunicorn's `--workers 4` flag (both forking processes)?

---

### Data Layer

#### 4.1 QuestDB vs TimescaleDB — which is the local default?

**RESOLVED:** QuestDB is the confirmed database for OHLCV data in Docker Compose.

After comprehensive benchmarking (see `ohlcv_database.md`), QuestDB was selected because:
- **1.7x faster data ingestion** (332K vs 194K rows/sec)
- **4.7x faster aggregations** across all symbols (critical for multi-asset backtesting)
- **Purpose-built for financial markets** with native `SAMPLE BY`, `ASOF JOIN`, and `LATEST ON`
- **Local Docker deployment** eliminates the free-tier constraints that previously favored TimescaleDB

The `psycopg2` compatibility issues (no scrollable cursors) are not a concern because the stack uses `asyncpg` for all PostgreSQL wire protocol connections, which works correctly with QuestDB.

#### 4.2 QuestDB access protocol inconsistency

`backend_server.md` shows two different QuestDB access patterns:
- **Writes:** HTTP REST (Influx Line Protocol) via `session.post("http://localhost:9000/write", data=line)`
- **Reads:** PGWire protocol via `asyncpg.create_pool(dsn="postgresql://localhost:8812/qdb")`

**Question:** Which write protocol is canonical for QuestDB in QuantLens?
- ILP over HTTP (port 9000) — optimized for high-throughput ingestion
- ILP over TCP (port 9009) — even higher throughput, documented in ARCHITECTURE.md
- PGWire SQL INSERT (port 8812) — simple but lower throughput
- Are different protocols intended for different workloads (ILP for bulk ingestion, PGWire for ad-hoc writes)?

#### 4.3 MongoDB → DuckDB (RESOLVED)

**RESOLVED:** MongoDB has been replaced by **DuckDB** (embedded, in-process) for fundamentals and economic indicators.

MongoDB's official Docker image and the community server image both encountered persistent **connection errors** during local benchmarking — a common issue with Docker-containerized databases for desktop app use cases. DuckDB eliminates this class of problems entirely by running embedded in the Python process with zero configuration. See [fundamentals_database.md](fundamentals_database.md) for the full rationale.

The deployment architecture diagram in `ARCHITECTURE.md` has been updated to show DuckDB as an embedded database alongside LanceDB, separate from the Docker Compose services.

#### 4.4 PostgreSQL — single instance or separate per concern?

`ARCHITECTURE.md` shows a single PostgreSQL instance for Strategies, Backtest Results, and User Data. But Huey also uses Redis (not PostgreSQL) as its result backend (`task_queue.md`). Meanwhile, `backend_server.md` references `asyncpg` connections to both PostgreSQL and QuestDB (PGWire).

**Question:** How many PostgreSQL-compatible connections does the Raw ASGI app maintain?
- One `asyncpg` pool for PostgreSQL (strategies, results, users)
- One `asyncpg` pool for QuestDB (OHLCV via PGWire on port 8812)
- Are these pools configured separately, or does a connection manager abstract them?

---

### Real-Time Data Flow

#### 5.1 WebSocket fan-in/fan-out architecture

`ARCHITECTURE.md`'s Data Flow Architecture shows a "Data Ingestion Service" with a "Data Normalizer" component connecting Tiingo/Finnhub/Alpaca WebSocket streams to QuestDB and Redis. This service needs to simultaneously serve real-time data to the React frontend via WebSocket.

**Question:** Is the data ingestion service a separate process from the Raw ASGI API, or is it part of the same Gunicorn+Uvicorn server?
- If separate, where does it run? Another Docker container? A Huey worker?
- If co-located with the Raw ASGI API, how does it manage long-lived outbound WebSocket connections (to Finnhub/Alpaca) alongside inbound HTTP requests?

#### 5.2 Finnhub WebSocket data type mismatch

The data ingestion service would subscribe to `"BINANCE:BTCUSDT"` on Finnhub's WebSocket, which is a crypto trade stream. But `data_providers.md` says Finnhub **Stock Candles (OHLCV) and Tick Data are Premium-only** on the free tier, and the free WebSocket provides real-time **trade streaming** (not OHLCV bars).

**Question:** Raw Finnhub WebSocket data is individual trades, not OHLCV bars.
- Is the OHLCV bar generation happening in QuestDB (via `SAMPLE BY`) or in the Python ingestion layer?
- Should the ingestion service insert into a `trades` table instead of `ohlcv_1m`?
- What does the schema look like for raw trade ingestion vs aggregated bars?

---

### Strategy Execution Security

#### 7.1 Where is Python strategy code executed?

`ARCHITECTURE.md` mentions "Sandbox Python execution (restricted environment, no network access)" under Security Considerations. The Monaco Editor flow shows strategy code sent to the Raw ASGI API for validation, then to NautilusTrader for execution.

**Question:** What sandboxing mechanism is used, and at which layer?
- `todos.md` lists this as "Not Started" — is there an interim plan for MVP?
- Does the Huey worker run user code in an unrestricted Python process?
- If strategies run inside Docker containers (Huey workers), does Docker provide sufficient isolation, or is additional sandboxing (RestrictedPython, nsjail, Pyodide server-side) needed?
- Is the threat model "malicious user code" (multi-tenant) or "accidental harmful code" (single-user local app)?

For a local-first single-user app, the threat model is arguably just accidental harm (infinite loops, excessive memory). Full sandboxing may be overengineered for MVP.

---

### BacktestEngine vs BacktestNode — Which API for Which Workflow?

#### 11.1 Conflicting API recommendations across docs

`core_engine.md` presents two NautilusTrader APIs for different workflows:
- **`BacktestEngine`** (low-level) — for rapid research iteration with `engine.reset()` for tight loops without process restart
- **`BacktestNode`** (high-level) — for production backtests via Huey workers, one per process

But `ARCHITECTURE.md` Section 2 ("NautilusTrader Integration") only mentions `BacktestEngine` in the class diagram (`NautilusBacktestService` has a `+BacktestEngine engine` field), while the Key Implementation Recommendations say: "Use `BacktestEngine` (low-level, fine-grained control) **or** `BacktestNode` with `BacktestRunConfig` objects (high-level, recommended for production)."

Meanwhile, `task_queue.md` exclusively uses `BacktestNode` in its Huey integration example:

```python
@huey.task(retries=2)
def run_nautilus_backtest(data, strategy_id, config):
    node = BacktestNode(configs=config)
    node.run()
    return node.get_results()
```

And `core_engine.md`'s own Huey example uses `BacktestNode`:

```python
results = [run_backtest(strategy_id, params) for params in param_grid]  # Huey parallel dispatch
```

**Question:** Which API is used where?
- Is `BacktestEngine` used in the API process for quick "validate strategy" dry runs, while `BacktestNode` is used in Huey workers for full backtests?
- The `ARCHITECTURE.md` class diagram shows `BacktestEngine` in `NautilusBacktestService` — is this service instantiated in the API process or in Huey workers?
- Can `BacktestEngine.reset()` be used inside a Huey process worker to reuse the engine across multiple tasks, or does `worker_max_tasks_per_child=50` (from `task_queue.md`) mean each worker gets a fresh `BacktestNode` per task?

---

### Data Pipeline: QuestDB → Parquet → ParquetDataCatalog

#### 12.1 Export mechanism undefined

`core_engine.md` states: "One data pipeline (QuestDB → Parquet → `ParquetDataCatalog`)" and the integration table says: "Historical OHLCV stored in QuestDB (local Docker), exported to Parquet for NautilusTrader's native data catalog."

But no document specifies **how** data moves from QuestDB to Parquet files:

**Question:** What is the QuestDB → Parquet export mechanism?
- QuestDB supports native Parquet export via `COPY` SQL command — is this used?
- Is there a scheduled Huey crontab task that periodically exports from QuestDB to Parquet? (Huey has built-in `crontab()` — no separate scheduler process needed.)
- Or does a data ingestion service write to both QuestDB and Parquet simultaneously (dual-write)?
- If the export is periodic, how does the system ensure Parquet files are up-to-date when a backtest starts? Does the backtest task trigger an export before running?

#### 12.2 Parquet catalog path and Docker volume mapping

`data_providers.md` shows `ParquetDataCatalog(path="/data/validated")` as the catalog path. But NautilusTrader runs inside Huey workers, which run in Docker containers.

**Question:** How does the Parquet catalog path map to Docker volumes?
- Is `/data/validated` a Docker volume shared between the data ingestion service and Huey workers?
- If QuestDB runs in one container and Huey workers in another, how does the export + catalog read work across containers?
- Does `docker compose` define a shared volume for the Parquet catalog?

---

### Parameter Sweep Scalability

#### 13.1 Huey worker count vs parameter grid size

`core_engine.md` claims: "QuantLens's target users run hundreds to low thousands of combinations — well within Huey worker parallelism." The Huey config in `task_queue.md` sets `--workers 4 --worker-type process`.

**Question:** With 4 concurrent workers and "hundreds to low thousands" of parameter combinations, what's the expected sweep duration?
- A 500-combination sweep with 4 workers = 125 sequential batches. If each backtest takes 5 seconds, that's ~10 minutes. Is this acceptable for research iteration?
- Does the system provide sweep progress in the UI (e.g., "234/500 complete, ~4 min remaining")? Progress can be tracked via Redis pub/sub.
- Is there a UI for configuring the parameter grid, or does the user write the grid in Python code in the Monaco editor?

#### 13.2 Memory pressure from parallel BacktestNodes

`core_engine.md` notes VectorBT's limitation: "Large parameter grids over many assets can exceed available RAM." NautilusTrader's BacktestNode loads the `ParquetDataCatalog` per process.

**Question:** With 4 Huey process workers, each loading a full `ParquetDataCatalog` for the same universe of symbols:
- Does each worker load a separate copy of the data into memory, or does NautilusTrader use memory-mapped files?
- For a 500-symbol × 20-year daily OHLCV catalog (~36M rows), what's the per-worker memory footprint?
- `task_queue.md` sets `worker_max_tasks_per_child=50` to mitigate memory leaks — does this force a full data reload every 50 tasks?

---

### Strategy Code: Authoring, Validation, and Execution

#### 14.1 Strategy template system undefined

`core_engine.md` states: "QuantLens provides strategy templates and Monaco editor autocompletion to reduce NautilusTrader's boilerplate." The integration table says: "Strategy templates target NautilusTrader's `TradingStrategy` API exclusively — one template system, one validation path."

But no document defines the template system:

**Question:** What does the strategy template system look like?
- Are templates pre-built `.py` files served by the Raw ASGI API (`GET /api/strategies/template`)?
- Do templates include the full NautilusTrader boilerplate (`Strategy` base class, `on_start`, `on_bar`, `on_stop` methods), leaving the user to fill in logic?
- Are there multiple templates (SMA crossover, momentum, mean reversion) or a single generic template?
- How does the Monaco editor provide NautilusTrader-specific autocompletion? `ARCHITECTURE.md` mentions a custom `CompletionItemProvider` but doesn't specify what completions are offered.

#### 14.2 Strategy validation — dry run vs AST parse

`core_engine.md` says strategies are validated before execution. `ARCHITECTURE.md`'s Monaco Editor flow shows two validation stages:
1. Client-side: Pyodide WASM AST parsing for syntax errors
2. Server-side: `POST /api/strategies/validate → Raw ASGI API → Nautilus: Dry-run parse`

**Question:** What does "dry-run parse" mean for NautilusTrader?
- Does NautilusTrader have a built-in strategy validation API, or does QuantLens implement this by importing the user's class and checking for required method signatures?
- Does the dry-run actually instantiate a `BacktestEngine` with no data, or is it purely a Python import + introspection?
- If the dry-run imports user code, this executes arbitrary Python in the API process. Does this conflict with the sandboxing concern (question 7.1)?

#### 14.3 Strategy code serialization for Huey

`core_engine.md` shows strategies executed in Huey workers. Huey uses pickle serialization by default, but for simplicity and security, strategy IDs (not code) should be passed as task arguments.

**Question:** How does strategy code get from the Monaco editor to a Huey worker?
- Is the Python source stored in PostgreSQL (`STRATEGIES.python_code` column in `ARCHITECTURE.md` schema), and the worker loads it by ID from the database?
- Or is the source code passed directly in the Huey task arguments?
- If loaded from the database, the worker must `exec()` or `importlib` the code at runtime — how does this interact with NautilusTrader's strategy registration?

---

### NautilusTrader Data Types vs Provider Data

#### 15.1 Adapter pattern — who converts, when?

`core_engine.md` integration table: "Single adapter pattern normalizing Tiingo/Alpaca/Finnhub data to NautilusTrader `Bar`/`QuoteTick` types."

But the data flow in `ARCHITECTURE.md` and `data_providers.md` shows data going through multiple stages: Provider → Validation → QuestDB → Parquet → ParquetDataCatalog.

**Question:** At which stage does the conversion to NautilusTrader types happen?
- During ingestion (provider response → NautilusTrader `Bar` objects → Parquet)?
- During catalog read (Parquet files → NautilusTrader `Bar` objects inside `ParquetDataCatalog`)?
- If conversion happens at ingestion time, the QuestDB and Parquet stores contain NautilusTrader-formatted data. Does this lock the storage schema to NautilusTrader's format?
- If conversion happens at read time, the Parquet files store a QuantLens-native schema and the catalog adapter converts on-the-fly. Is this the intended design?

---

### skfolio Integration Boundary

#### 17.1 Results handoff between NautilusTrader and skfolio

`core_engine.md` System Flow diagram shows: `Validation → Portfolio Layer → Strategy Returns → skfolio Optimization → Allocation Weights`. The integration table says: "Portfolio optimization runs independently of the backtest engine — NautilusTrader produces trade results, skfolio optimizes allocations."

But no document specifies the data contract between NautilusTrader results and skfolio inputs.

**Question:** What is the NautilusTrader → skfolio handoff format?
- skfolio's `MeanRisk` optimizer expects a DataFrame of asset returns (rows = time observations, columns = assets). NautilusTrader produces per-strategy trade results and equity curves.
- Who converts NautilusTrader's trade-level results into asset-level return series for skfolio?
- If a user runs 5 strategies on different asset universes, does skfolio optimize across all 5 strategies (strategy-level allocation) or across underlying assets (asset-level allocation)?
- Is the conversion logic in the Raw ASGI backend, in a Huey task, or in the React frontend?

---

### Data Provider Contradictions

#### 9.1 Tiingo rate limits — inconsistent across docs

`ARCHITECTURE.md` says "Tiingo limits are plan-dependent (hourly requests + daily requests + monthly bandwidth — see pricing page)." `data_providers.md` lists "50 requests/hour · 1,000 requests/day · 500 unique symbols/month." The `todos.md` completed item says "'50 req/hr' was incorrect."

**Question:** If `50 req/hr` is incorrect per todos.md, what's the actual Tiingo free-tier rate limit? And should `data_providers.md`'s table be corrected?

---

### Live/Paper Trading Path

#### 18.1 Backtest-live parity claim vs current architecture

`core_engine.md`'s key selling point: "Backtest-live parity — identical strategy code runs in backtest and live modes with zero changes." The decision rationale states this is "the most important factor for a tool targeting serious quants."

But no other document describes the live trading architecture:

**Question:** How does the live trading path work in QuantLens?
- `core_engine.md` mentions broker adapters (Binance, Interactive Brokers, OKX, Bybit) — are any of these configured in QuantLens, or is live trading entirely future scope?
- `data_providers.md` says Alpaca is for "paper trading only." Does QuantLens have a paper trading mode, or is this deferred?
- If live/paper trading is future scope, should `core_engine.md` explicitly label it as such to avoid setting incorrect expectations about MVP capabilities?

---

## Frontend

### Frontend ↔ API Layer Communication

#### 1.1 Who owns the API process — Tauri or Docker?

`ARCHITECTURE.md` shows the Raw ASGI API inside the "Docker Compose (Local)" subgraph (Deployment Architecture diagram), implying it runs as a Docker container. But `local_frontend.md` shows a direct `Tauri (Rust Core) → Gunicorn+Uvicorn Raw ASGI (Localhost)` connection, which reads like the API runs as a bare localhost process outside Docker.

**Question:** Does the Raw ASGI API run inside a Docker container (alongside PostgreSQL, QuestDB, Redis, etc.) or as a native process managed by Tauri's Rust backend? This affects:
- Port binding and networking (container-to-container vs host-to-host)
- Startup orchestration (does `docker compose up` start the API, or does Tauri launch it?)
- Development workflow (hot-reload inside Docker vs native `uvicorn --reload`)

#### 1.2 How does the Tauri app discover the backend?

`local_frontend.md` hardcodes `ws://localhost:8000/ws/backtest` and `http://localhost:3000` (React dev server in CORS config). All backend communication goes through the single Gunicorn+Uvicorn Raw ASGI endpoint on port 8000.

**Question:** What's the service discovery mechanism?
- Are ports hardcoded in the frontend, or does Tauri's Rust backend provide them via IPC?
- If the API is in Docker, does the container expose ports to the host, or does Tauri communicate via Docker networking?

#### 1.3 CORS configuration contradiction

`backend_server.md` production config runs on port 8000 (Gunicorn+Uvicorn). The Vite dev server runs on `http://localhost:3000`. But in production, the Tauri webview loads from a `tauri://` or `https://tauri.localhost` origin, not `http://localhost:3000`.

**Question:** What's the CORS strategy for the production Tauri build? Options:
- Tauri's Rust backend proxies all API calls (no CORS needed)
- The Raw ASGI app allows the Tauri-specific origin
- CORS is disabled entirely since both run locally on the same machine

---

### Tauri Integration

#### 6.1 Tauri's Rust backend — used or unused?

`local_frontend.md` positions Tauri as a lightweight shell wrapping a WebView. But Tauri v2's Rust backend can handle IPC commands, file system access, and even HTTP requests via its plugin system.

**Question:** Does QuantLens use any Tauri Rust commands (`#[tauri::command]`)?
- File operations (loading/saving strategy files locally)?
- System monitoring (CPU/memory usage of backtest workers)?
- Native notifications (backtest complete)?
- Or is Tauri purely a WebView container with all logic in the React SPA + Raw ASGI backend?

#### 6.2 Tauri + Docker Compose startup orchestration

The user story says `docker compose up` starts all backend services. But Tauri is a native desktop app, not a Docker container.

**Question:** What's the startup sequence?
1. User runs `docker compose up` manually, then opens the Tauri app?
2. Tauri's Rust backend runs `docker compose up` on launch?
3. A launcher script starts both Docker Compose and the Tauri app?
- What happens if Docker services aren't running when the Tauri app opens? Is there a health check / connection retry UI?

---

### Real-Time Data Delivery

#### 5.3 Market data for the React frontend — REST or WebSocket?

`ARCHITECTURE.md` Data Flow Architecture shows: `Tiingo/Finnhub/Alpaca → Raw ASGI WebSocket → Market Data Hook → Price Ticker Component`. But `local_frontend.md` shows TanStack Query handling REST data, with WebSocket pushing into TanStack Query cache.

**Question:** Does the React frontend get live market data via:
- A dedicated WebSocket connection (as shown in ARCHITECTURE.md)?
- REST polling with TanStack Query's `refetchInterval`?
- WebSocket updates pushed into TanStack Query cache (as shown in local_frontend.md)?
- All three, depending on the data type?

---

### Custom Dataset Upload

#### 10.1 Custom dataset upload pipeline

`user_stories.md` includes "bring-your-own data (custom datasets I upload via the app UI)." `todos.md` lists this as "Not Started." Neither `ARCHITECTURE.md` nor `backend_server.md` specifies the upload flow.

**Question:** What's the planned pipeline?
- File format support (CSV, Parquet, Excel)?
- Validation rules (required columns, timestamp format, data quality checks)?
- Storage destination (direct to ParquetDataCatalog, or QuestDB first)?
- UI component (drag-and-drop in the Tauri app)?

---

## Others

### Platform App Integration (Future)

#### 8.1 What data flows from local app to platform?

`ARCHITECTURE.md` shows: `QuantLens Local App → Submit Results · Deploy Strategy → TanStack Start + React → Neon PostgreSQL`. But there's no specification of what "submit results" means.

**Question:**
- Does the local app upload raw backtest results (trades, equity curves, metrics)?
- Does it upload the strategy code itself?
- Is there an API contract between the local app and the platform app?
- Authentication: How does the local app authenticate with the deployed platform?

---

### Authentication and Authorization

#### 10.2 Authentication model

`ARCHITECTURE.md` database schema includes a `USERS` table. Some earlier docs mention JWT auth. But for a local-first single-user desktop app:

**Question:** Is authentication needed for the local app?
- If single-user, why is there a USERS table?
- Is the USERS table only for the future platform app?
- Does JWT auth protect the local API endpoints, or is it only for the deployed platform?
- If no auth locally, what prevents other processes on the same machine from accessing the API?

---

### Error Handling and Retry Strategy

#### 10.3 Error handling and retry strategy

`backend_server.md`'s database patterns show direct `asyncpg` usage without explicit retry logic. `task_queue.md` shows Huey retry with `@huey.task(retries=2, retry_delay=30)`.

**Question:** What's the unified error handling strategy?
- Data provider connection failures: exponential backoff? circuit breaker?
- Backtest failures: retry automatically or report to user?
- QuestDB write failures: buffer in Redis and retry, or drop?
- WebSocket disconnections from the React frontend: auto-reconnect with what backoff?
