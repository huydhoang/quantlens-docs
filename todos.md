# Integration Questions

Cross-referencing [system_design.md](system_design.md), [local_frontend.md](local_frontend.md), and [asgi_web_server.md](asgi_web_server.md) against all other architecture docs surfaced the following open questions. See [integration_questions.md](integration_questions.md) for full context on each item.

## Frontend ↔ API Layer Communication
- [ ] **1.1 API process ownership** — Does FastAPI run inside Docker Compose or as a native process managed by Tauri? Affects port binding, startup orchestration, and dev workflow.
- [ ] **1.2 Service discovery** — How does the Tauri app discover FastAPI (and the Tier 2 gateway on port 8001)? Hardcoded ports, Tauri IPC, or Docker networking?
- [ ] **1.3 CORS configuration** — `asgi_web_server.md` allows `http://localhost:3000` (Vite dev), but production Tauri uses `tauri://` or `https://tauri.localhost`. What's the production CORS strategy?

## Backtest Execution: FastAPI ↔ Celery ↔ NautilusTrader
- [ ] **2.1 Frontend → backtest path** — Contradictory diagrams: one shows `Frontend → FastAPI → Celery`, another shows `Tauri → Redis` directly. Which is canonical?
- [ ] **2.2 WebSocket progress ownership** — Does Tier 1 (FastAPI) or Tier 2 (vanilla ASGI on 8001) own the backtest progress WebSocket?
- [ ] **2.3 NautilusKernel in FastAPI lifespan** — `asgi_web_server.md` initializes a kernel in FastAPI, but all docs say backtests run in Celery workers. What does the FastAPI-hosted kernel do?
- [ ] **2.4 ProcessPoolExecutor vs Celery** — Both are used for CPU-bound work. What's the decision boundary (skfolio in-process vs backtests in Celery)? Does `ProcessPoolExecutor` conflict with `uvicorn --workers 4`?

## Two-Tier Architecture
- [ ] **3.1 MVP scope** — Is the two-tier setup (FastAPI on 8000 + vanilla ASGI on 8001) for MVP or future? `system_design.md` and `local_frontend.md` show only a single API layer.
- [ ] **3.2 Shared NautilusTrader kernel** — The two-tier diagram shows a "shared" kernel, but NautilusTrader enforces one-BacktestNode-per-process. How do two Uvicorn processes share it?

## Data Layer
- [ ] **4.1 QuestDB vs TimescaleDB** — `system_design.md` uses QuestDB; `ohlcv_database.md` recommends TimescaleDB for Phase 1. Which ships in Docker Compose?
- [ ] **4.2 QuestDB write protocol** — Three patterns shown: ILP over HTTP (port 9000), ILP over TCP (port 9009), PGWire SQL INSERT (port 8812). Which is canonical, or are different protocols for different tiers?
- [ ] **4.3 MongoDB presence** — Deployment diagram includes MongoDB; main Local App diagram omits it. Is MongoDB confirmed for the local Docker stack or deferred?
- [ ] **4.4 PostgreSQL connection pools** — How many asyncpg pools does FastAPI maintain (PostgreSQL + QuestDB PGWire + potentially TimescaleDB)?

## Real-Time Data Flow
- [ ] **5.1 Data ingestion service** — Is the Tier 2 vanilla ASGI gateway also the data ingestion service, or is ingestion a separate process?
- [ ] **5.2 Finnhub trade → OHLCV mismatch** — Tier 2 inserts into `ohlcv_1m`, but Finnhub WebSocket delivers raw trades. Where does bar aggregation happen (QuestDB `SAMPLE BY` or Python)?
- [ ] **5.3 Frontend market data delivery** — Does the React frontend get live data via dedicated WebSocket, REST polling, or WebSocket → TanStack Query cache?

## Tauri Integration
- [ ] **6.1 Tauri Rust backend usage** — Is Tauri purely a WebView shell, or does it use `#[tauri::command]` for file I/O, system monitoring, or native notifications?
- [ ] **6.2 Startup orchestration** — Does the user run `docker compose up` manually before opening Tauri, or does Tauri launch Docker on startup? What's the health check / retry UX?

## Strategy Execution Security
- [ ] **7.1 Sandboxing mechanism** — `system_design.md` says "restricted environment, no network access" but specifies no mechanism. What's the interim plan for MVP? For a single-user local app, is the threat model accidental harm (infinite loops) rather than malicious code?

## Platform App Integration (Future)
- [ ] **8.1 Local → platform data flow** — What exactly is "submit results"? Raw trades, equity curves, strategy code? What's the API contract and auth model between local and platform apps?

## Data Provider Contradictions
- [ ] **9.1 Tiingo rate limits** — `data_providers.md` lists "50 requests/hour" but this was previously identified as incorrect (limits are plan-dependent). Correct the table to match actual free-tier limits.

## Missing Specifications
- [ ] **10.1 Custom dataset upload** — Design the file upload → validation → Parquet conversion → ParquetDataCatalog registration pipeline (file formats, validation rules, storage destination, UI component).
- [ ] **10.2 Authentication model** — Is auth needed for the local app? The USERS table and JWT auth are mentioned, but a single-user desktop app may not need them.
- [ ] **10.3 Error handling / retry strategy** — Define unified approach for data provider failures, backtest failures, QuestDB write failures, and frontend WebSocket reconnection.
