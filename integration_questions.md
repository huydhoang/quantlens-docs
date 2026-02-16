# Integration Questions: system_design.md × local_frontend.md × asgi_web_server.md

Deep-dive cross-referencing of the three core architecture documents surfaced the following integration questions, contradictions, and unresolved design decisions.

---

## 1. Frontend ↔ API Layer Communication

### 1.1 Who owns the API process — Tauri or Docker?

`system_design.md` shows FastAPI inside the "Docker Compose (Local)" subgraph (Deployment Architecture diagram), implying it runs as a Docker container. But `local_frontend.md` shows a direct `Tauri (Rust Core) → FastAPI + Uvicorn (Localhost)` connection, which reads like FastAPI runs as a bare localhost process outside Docker.

**Question:** Does FastAPI run inside a Docker container (alongside PostgreSQL, QuestDB, Redis, etc.) or as a native process managed by Tauri's Rust backend? This affects:
- Port binding and networking (container-to-container vs host-to-host)
- Startup orchestration (does `docker compose up` start FastAPI, or does Tauri launch it?)
- Development workflow (hot-reload of FastAPI inside Docker vs native `uvicorn --reload`)

### 1.2 How does the Tauri app discover the FastAPI backend?

`local_frontend.md` hardcodes `ws://localhost:8000/ws/backtest` and `http://localhost:3000` (React dev server in CORS config). `asgi_web_server.md` adds a second service on port 8001 for the real-time gateway.

**Question:** What's the service discovery mechanism?
- Are ports hardcoded in the frontend, or does Tauri's Rust backend provide them via IPC?
- If FastAPI is in Docker, does the container expose ports to the host, or does Tauri communicate via Docker networking?
- In the two-tier architecture (port 8000 + 8001), how does the React SPA know which WebSocket endpoint to connect to for backtest progress (Tier 1) vs market data (Tier 2)?

### 1.3 CORS configuration contradiction

`asgi_web_server.md` sets `allow_origins=["http://localhost:3000"]` (Vite dev server). But in production, the Tauri webview loads from a `tauri://` or `https://tauri.localhost` origin, not `http://localhost:3000`.

**Question:** What's the CORS strategy for the production Tauri build? Options:
- Tauri's Rust backend proxies all API calls (no CORS needed)
- FastAPI allows the Tauri-specific origin
- CORS is disabled entirely since both run locally on the same machine

---

## 2. Backtest Execution: FastAPI ↔ Celery ↔ NautilusTrader

### 2.1 Contradictory communication paths between frontend and backtest engine

`system_design.md` (Local App diagram) shows: `Frontend → HTTP/WebSocket → API → Celery/Redis → Nautilus`. But the Deployment Architecture diagram in the same file shows: `Tauri Desktop App → Enqueue Jobs → Redis` (direct, bypassing FastAPI).

**Question:** Does the Tauri frontend enqueue Celery jobs directly to Redis, or does it go through FastAPI? Direct Redis access from the frontend:
- Bypasses validation, auth, and rate limiting
- Requires the Tauri Rust backend to speak the Celery protocol
- Contradicts the FastAPI-as-proxy pattern shown in the Backtest Execution Flow sequence diagram

### 2.2 WebSocket progress broadcasting — who pushes to the client?

The Backtest Execution Flow in `system_design.md` shows: `Worker → Queue → API → UI (WebSocket)`. But `asgi_web_server.md` (Tier 2) shows a Redis pub/sub pattern where the vanilla ASGI gateway subscribes to Redis channels and forwards to WebSocket clients.

**Question:** Which service owns the backtest progress WebSocket?
- **Tier 1 (FastAPI):** As shown in `system_design.md` — FastAPI manages WebSocket connections and receives progress from Celery/Redis
- **Tier 2 (Vanilla ASGI):** As shown in `asgi_web_server.md` — a separate process on port 8001 handles all WebSocket streaming

If it's Tier 1, then backtest progress and market data WebSockets live on different services (FastAPI vs vanilla ASGI). How does the frontend manage two separate WebSocket connections to two different ports?

### 2.3 NautilusTrader lifespan management in FastAPI

`asgi_web_server.md` Tier 1 implementation shows a `NautilusKernel` initialized in FastAPI's lifespan context:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.kernel = NautilusKernel()
    await app.state.kernel.start()
    yield
    await app.state.kernel.stop()
```

But `core_engine.md` and `system_design.md` both state that NautilusTrader runs **in Celery workers**, not in the FastAPI process. The system_design.md explicitly says: "NautilusTrader is a **library, not a service** — the API layer enqueues jobs to Celery; workers import and call `nautilus_trader` directly in-process."

**Question:** Is `NautilusKernel` in FastAPI's lifespan a mistake, or is it intentional for a different purpose (e.g., strategy validation, data catalog access)? If backtests run exclusively in Celery workers, what does the FastAPI-hosted kernel do?

### 2.4 ProcessPoolExecutor vs Celery for CPU-bound work

`asgi_web_server.md` shows:

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

`asgi_web_server.md` presents the hybrid two-tier setup (FastAPI on 8000 + Vanilla ASGI on 8001) as the recommended production architecture. But the final verdict says: "start with **FastAPI on Uvicorn**. When live trading is added, extract real-time endpoints."

The system_design.md and local_frontend.md show only a single API layer (FastAPI).

**Question:** Is the local desktop app (MVP) single-tier or two-tier?
- If single-tier, should `asgi_web_server.md`'s Tier 2 code be labeled as "future" to avoid confusion?
- If two-tier from day one, the Docker Compose config, frontend WebSocket management, and CORS setup all need to account for two backend services

### 3.2 NautilusKernel shared between tiers

The two-tier diagram in `asgi_web_server.md` shows both tiers connecting to a "Shared Layer" containing `NautilusTrader kernel`. But NautilusTrader enforces a **one-BacktestNode-per-process** constraint (documented in `system_design.md` and `core_engine.md`).

**Question:** How do two separate Uvicorn processes (Tier 1 + Tier 2) share a NautilusTrader kernel?
- Is the "shared" kernel a misconception? Each tier would need its own kernel instance
- Or is the kernel shared via Redis/IPC rather than in-process?
- Does Tier 2's `check_signal` function (which calls `self.nautilus.process_tick()`) require a full `BacktestNode`, or is it using a lighter-weight NautilusTrader component?

---

## 4. Data Layer Contradictions

### 4.1 QuestDB vs TimescaleDB — which is the local default?

`system_design.md` and `asgi_web_server.md` both use QuestDB as the OHLCV store. But `ohlcv_database.md` recommends **TimescaleDB for Phase 1** and QuestDB only for Phase 2+.

**Question:** Which time-series database ships with the local Docker Compose setup?
- The system_design.md diagrams all show QuestDB — does this mean the Phase 1 TimescaleDB recommendation was overridden?
- If QuestDB, how are the `psycopg2` compatibility issues (documented in `ohlcv_database.md` — no scrollable cursors) handled with asyncpg in the FastAPI stack?
- Should `ohlcv_database.md` be updated to reflect QuestDB as the default, or should system_design.md add TimescaleDB as the Phase 1 option?

### 4.2 QuestDB access protocol inconsistency

`asgi_web_server.md` shows two different QuestDB access patterns:
- **Writes:** HTTP REST (Influx Line Protocol) via `session.post("http://localhost:9000/write", data=line)`
- **Reads:** PGWire protocol via `asyncpg.create_pool(dsn="postgresql://localhost:8812/qdb")`
- **Tier 2 writes:** Also PGWire via `pool.execute("INSERT INTO ohlcv_1m ...")`

**Question:** Which write protocol is canonical for QuestDB in QuantLens?
- ILP over HTTP (port 9000) — optimized for high-throughput ingestion
- ILP over TCP (port 9009) — even higher throughput, documented in system_design.md
- PGWire SQL INSERT (port 8812) — shown in Tier 2 code
- Are different protocols used for different tiers (ILP for bulk ingestion, PGWire for individual tick writes)?

### 4.3 MongoDB placement in architecture

`system_design.md` mentions MongoDB in the Deployment Architecture diagram (`MongoDB — Fundamentals · Economic Indicators`). `asgi_web_server.md` shows MongoDB queries in the Tier 1 endpoints and the two-tier diagram. But `system_design.md`'s main Local App diagram does **not** include MongoDB — it only shows PostgreSQL and QuestDB.

**Question:** Is MongoDB a confirmed part of the local Docker stack?
- The main system diagram omits it, but the deployment diagram includes it
- Should the Local App flowchart be updated to include MongoDB?
- Or is MongoDB deferred to a later phase?

### 4.4 PostgreSQL — single instance or separate per concern?

`system_design.md` shows a single PostgreSQL instance for Strategies, Backtest Results, and User Data. But Celery also uses Redis (not PostgreSQL) as its result backend (`task_queue.md`). Meanwhile, `asgi_web_server.md` references `asyncpg` connections to both PostgreSQL and QuestDB (PGWire).

**Question:** How many PostgreSQL-compatible connections does the FastAPI app maintain?
- One `asyncpg` pool for PostgreSQL (strategies, results, users)
- One `asyncpg` pool for QuestDB (OHLCV via PGWire)
- Potentially one for TimescaleDB if it's the Phase 1 OHLCV store
- Are these pools configured separately, or does a connection manager abstract them?

---

## 5. Real-Time Data Flow

### 5.1 WebSocket fan-in/fan-out architecture

`asgi_web_server.md` Tier 2 shows individual WebSocket connections to Finnhub and Alpaca, with data published to Redis channels. But `system_design.md`'s Data Flow Architecture shows a separate "Data Ingestion Service" with a "Data Normalizer" component.

**Question:** Is the data ingestion service the same as the Tier 2 vanilla ASGI gateway, or is it a separate process?
- If they're the same, the Tier 2 gateway handles both ingestion (Finnhub/Alpaca → QuestDB) and serving (QuestDB → React frontend)
- If separate, where does the ingestion service run? Another Docker container? A Celery worker?

### 5.2 Finnhub WebSocket data type mismatch

`asgi_web_server.md` Tier 2 code subscribes to `"BINANCE:BTCUSDT"` on Finnhub's WebSocket, which is a crypto trade stream. But `data_providers.md` says Finnhub **Stock Candles (OHLCV) and Tick Data are Premium-only** on the free tier, and the free WebSocket provides real-time **trade streaming** (not OHLCV bars).

**Question:** The Tier 2 code inserts into `ohlcv_1m` table, but the raw Finnhub WebSocket data is individual trades, not OHLCV bars.
- Is the OHLCV bar generation happening in QuestDB (via `SAMPLE BY`) or in the Python ingestion layer?
- If in QuestDB, the `INSERT INTO ohlcv_1m` statement should be inserting into a `trades` table, not `ohlcv_1m`
- What does the schema look like for raw trade ingestion vs aggregated bars?

### 5.3 Market data for the React frontend — REST or WebSocket?

`system_design.md` Data Flow Architecture shows: `Tiingo/Finnhub/Alpaca → FastAPI WebSocket → Market Data Hook → Price Ticker Component`. But `local_frontend.md` shows TanStack Query handling REST data, with WebSocket pushing into TanStack Query cache.

**Question:** Does the React frontend get live market data via:
- A dedicated WebSocket connection (as shown in system_design.md)?
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

`system_design.md` mentions "Sandbox Python execution (restricted environment, no network access)" under Security Considerations. The Monaco Editor flow shows strategy code sent to FastAPI for validation, then to NautilusTrader for execution.

**Question:** What sandboxing mechanism is used, and at which layer?
- `todos.md` lists this as "Not Started" — is there an interim plan for MVP?
- Does the Celery worker run user code in an unrestricted Python process?
- If strategies run inside Docker containers (Celery workers), does Docker provide sufficient isolation, or is additional sandboxing (RestrictedPython, nsjail, Pyodide server-side) needed?
- Is the threat model "malicious user code" (multi-tenant) or "accidental harmful code" (single-user local app)?

For a local-first single-user app, the threat model is arguably just accidental harm (infinite loops, excessive memory). Full sandboxing may be overengineered for MVP.

---

## 8. Platform App Integration (Future)

### 8.1 What data flows from local app to platform?

`system_design.md` shows: `QuantLens Local App → Submit Results · Deploy Strategy → TanStack Start + React → Neon PostgreSQL`. But there's no specification of what "submit results" means.

**Question:**
- Does the local app upload raw backtest results (trades, equity curves, metrics)?
- Does it upload the strategy code itself?
- Is there an API contract between the local app and the platform app?
- Authentication: How does the local app authenticate with the deployed platform?

---

## 9. Data Provider Contradictions

### 9.1 Tiingo rate limits — inconsistent across docs

`system_design.md` says "Tiingo limits are plan-dependent (hourly requests + daily requests + monthly bandwidth — see pricing page)." `data_providers.md` lists "50 requests/hour · 1,000 requests/day · 500 unique symbols/month." The `todos.md` completed item says "'50 req/hr' was incorrect."

**Question:** If `50 req/hr` is incorrect per todos.md, what's the actual Tiingo free-tier rate limit? And should `data_providers.md`'s table be corrected?

---

## 10. Missing Specifications

### 10.1 Custom dataset upload pipeline

`user_stories.md` includes "bring-your-own data (custom datasets I upload via the app UI)." `todos.md` lists this as "Not Started." Neither `system_design.md` nor `asgi_web_server.md` specifies the upload flow.

**Question:** What's the planned pipeline?
- File format support (CSV, Parquet, Excel)?
- Validation rules (required columns, timestamp format, data quality checks)?
- Storage destination (direct to ParquetDataCatalog, or QuestDB first)?
- UI component (drag-and-drop in the Tauri app)?

### 10.2 Authentication and authorization

`system_design.md` database schema includes a `USERS` table. `asgi_web_server.md`'s two-tier diagram mentions "JWT auth" for Tier 1. But for a local-first single-user desktop app:

**Question:** Is authentication needed for the local app?
- If single-user, why is there a USERS table?
- Is the USERS table only for the future platform app?
- Does JWT auth protect the local FastAPI endpoints, or is it only for the deployed platform?
- If no auth locally, what prevents other processes on the same machine from accessing the API?

### 10.3 Error handling and retry strategy

`asgi_web_server.md` Tier 2 shows a bare `except Exception` with a 5-second reconnect delay for Finnhub WebSocket failures. `task_queue.md` shows Celery retry with `max_retries=2` and `countdown=30`.

**Question:** What's the unified error handling strategy?
- Data provider connection failures: exponential backoff? circuit breaker?
- Backtest failures: retry automatically or report to user?
- QuestDB write failures: buffer in Redis and retry, or drop?
- WebSocket disconnections from the React frontend: auto-reconnect with what backoff?
