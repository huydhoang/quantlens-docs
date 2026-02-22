# Integration Questions

Cross-referencing [ARCHITECTURE.md](ARCHITECTURE.md), [local_frontend.md](local_frontend.md), [backend_server.md](backend_server.md), and [core_engine.md](core_engine.md) against all other architecture docs surfaced the following open questions. See [integration_questions.md](integration_questions.md) for full context on each item.

**Priority levels:**
- 🔴 **P0 — Blocks MVP**: Architectural contradictions that must be resolved before implementation can begin
- 🟠 **P1 — MVP Required**: Key decisions needed during implementation; won't block scaffolding but blocks feature completion
- 🟡 **P2 — Pre-launch**: Should resolve before shipping v1 but can iterate on during development
- 🟢 **P3 — Future**: Explicitly deferred, future-phase, or nice-to-have clarifications

---

## Backend

### Backtest Execution: API Layer ↔ Huey ↔ NautilusTrader
- [ ] 🔴 **2.1 Frontend → backtest path** — Contradictory diagrams: one shows `Frontend → Raw ASGI API → Huey`, another shows `Tauri → Redis` directly. Which is canonical?
- [ ] 🟠 **2.2 WebSocket progress ownership** — How does the Raw ASGI API manage per-client Redis pub/sub subscriptions for backtest progress forwarding over WebSocket?
- [ ] 🔴 **2.3 NautilusTrader in API lifespan** — Earlier examples show a NautilusKernel initialized in the API lifespan, but all docs say backtests run in Huey workers. Should any NautilusTrader component live in the Raw ASGI API process?
- [ ] 🟠 **2.4 ProcessPoolExecutor vs Huey** — Both are used for CPU-bound work. What's the decision boundary (skfolio in-process vs backtests in Huey)? Does `ProcessPoolExecutor` conflict with Gunicorn `--workers 4`?

### Data Layer
- [x] 🔴 **4.1 QuestDB vs TimescaleDB** — `ARCHITECTURE.md` uses QuestDB; `ohlcv_database.md` recommends TimescaleDB for Phase 1. Which ships in Docker Compose? **RESOLVED: QuestDB** (see ohlcv_database.md for benchmark-driven decision)
- [ ] 🟠 **4.2 QuestDB write protocol** — Three patterns shown: ILP over HTTP (port 9000), ILP over TCP (port 9009), PGWire SQL INSERT (port 8812). Which is canonical for bulk ingestion vs ad-hoc writes?
- [x] 🟠 **4.3 MongoDB → DuckDB** — MongoDB Docker container had persistent connection errors during local benchmarking. **RESOLVED: DuckDB** (embedded, in-process) replaces MongoDB for fundamentals and economic indicators. See fundamentals_database.md for rationale.
- [ ] 🟡 **4.4 PostgreSQL connection pools** — How many asyncpg pools does the Raw ASGI API maintain (PostgreSQL + QuestDB PGWire)?

### Real-Time Data Flow
- [ ] 🟠 **5.1 Data ingestion service** — Is the data ingestion service a separate process from the Raw ASGI API, or are long-lived outbound WebSocket connections managed within the same Gunicorn workers?
- [ ] 🟠 **5.2 Finnhub trade → OHLCV mismatch** — Finnhub WebSocket delivers raw trades, not OHLCV bars. Should ingestion write to a `trades` table with bar aggregation via QuestDB `SAMPLE BY`, rather than inserting directly into `ohlcv_1m`?

### Strategy Execution Security
- [ ] 🟡 **7.1 Sandboxing mechanism** — `ARCHITECTURE.md` says "restricted environment, no network access" but specifies no mechanism. What's the interim plan for MVP? For a single-user local app, is the threat model accidental harm (infinite loops) rather than malicious code?

### BacktestEngine vs BacktestNode
- [ ] 🔴 **11.1 BacktestEngine vs BacktestNode API assignment** — `ARCHITECTURE.md` class diagram uses `BacktestEngine`; `task_queue.md` uses `BacktestNode` in Huey. Which API is used where (API process validation vs Huey execution)? Can `BacktestEngine.reset()` reuse engines within process workers?

### Data Pipeline: QuestDB → Parquet → ParquetDataCatalog
- [ ] 🔴 **12.1 QuestDB → Parquet export mechanism** — `core_engine.md` assumes a QuestDB → Parquet → ParquetDataCatalog pipeline but no doc specifies how data moves from QuestDB to Parquet files (COPY command, Huey crontab job, or dual-write).
- [ ] 🟠 **12.2 Parquet catalog Docker volume** — Huey workers and the data ingestion service need shared access to `/data/validated`. How is this mapped in Docker Compose?

### Parameter Sweep Scalability
- [ ] 🟡 **13.1 Parameter sweep duration** — With 4 Huey workers and hundreds of combinations, what's the expected sweep time? Is there UI progress for sweeps?
- [ ] 🟡 **13.2 Memory pressure from parallel BacktestNodes** — 4 process workers each loading a full ParquetDataCatalog. Does NautilusTrader use memory-mapped files, or does each worker hold a separate copy?

### Strategy Code: Authoring, Validation, and Execution
- [ ] 🟠 **14.1 Strategy template system** — Referenced in `core_engine.md` and `ARCHITECTURE.md` but never defined. What templates exist? What Monaco completions are offered?
- [ ] 🟠 **14.2 Strategy dry-run validation** — What does NautilusTrader "dry-run parse" mean? Does it execute user code in the Raw ASGI API process, conflicting with sandboxing (7.1)?
- [ ] 🔴 **14.3 Strategy code serialization** — Huey uses pickle serialization by default, but strategy code should travel as IDs not source. How does code travel from Monaco → PostgreSQL → Huey worker → NautilusTrader?

### NautilusTrader Data Types vs Provider Data
- [ ] 🟠 **15.1 Data type conversion stage** — At which pipeline stage are provider responses converted to NautilusTrader `Bar`/`QuoteTick` types (ingestion time vs catalog read time)?

### skfolio Integration Boundary
- [ ] 🟠 **17.1 NautilusTrader → skfolio handoff** — No doc defines how NautilusTrader trade results are converted to the asset-return DataFrames that skfolio expects. Who does the conversion, and where?

### Data Provider Contradictions
- [x] 🟠 **9.1 Tiingo rate limits** — `data_providers.md` lists "50 requests/hour" but this was previously identified as incorrect (limits are plan-dependent). Correct the table to match actual free-tier limits. **RESOLVED:** "50 req/hr" was incorrect — limits are plan-dependent. `ARCHITECTURE.md` updated to reflect plan-dependent limits. `data_providers.md` table updated to `~1,000 req/day (free)` and the `RateLimitedClient` example updated to `max_per_day=1000`.

### Live/Paper Trading Path
- [ ] 🟢 **18.1 Live/paper trading scope** — `core_engine.md` highlights backtest-live parity as the key value prop, but no doc describes the live trading path. Is this MVP or future? Should docs explicitly label it?

---

## Frontend

### Frontend ↔ API Layer Communication
- [ ] 🔴 **1.1 API process ownership** — Does the Raw ASGI API run inside Docker Compose or as a native process managed by Tauri? Affects port binding, startup orchestration, and dev workflow.
- [ ] 🟡 **1.2 Service discovery** — How does the Tauri app discover the Raw ASGI API on `localhost:8000`? Hardcoded port, Tauri IPC, or Docker networking?
- [ ] 🟡 **1.3 CORS configuration** — `backend_server.md` production config uses port 8000 with Gunicorn+Uvicorn, but production Tauri uses `tauri://` or `https://tauri.localhost`. What's the production CORS strategy?

### Tauri Integration
- [ ] 🟡 **6.1 Tauri Rust backend usage** — Is Tauri purely a WebView shell, or does it use `#[tauri::command]` for file I/O, system monitoring, or native notifications?
- [ ] 🟠 **6.2 Startup orchestration** — Does the user run `docker compose up` manually before opening Tauri, or does Tauri launch Docker on startup? What's the health check / retry UX?

### Real-Time Data Delivery
- [ ] 🟡 **5.3 Frontend market data delivery** — Does the React frontend get live data via dedicated WebSocket, REST polling, or WebSocket → TanStack Query cache?

### Custom Dataset Upload
- [ ] 🟡 **10.1 Custom dataset upload** — Design the file upload → validation → Parquet conversion → ParquetDataCatalog registration pipeline (file formats, validation rules, storage destination, UI component).

---

## Others

### Platform App Integration (Future)
- [ ] 🟢 **8.1 Local → platform data flow** — What exactly is "submit results"? Raw trades, equity curves, strategy code? What's the API contract and auth model between local and platform apps?

### Authentication and Authorization
- [ ] 🟡 **10.2 Authentication model** — Is auth needed for the local app? The USERS table and JWT auth are mentioned, but a single-user desktop app may not need them.

### Error Handling and Retry Strategy
- [ ] 🟡 **10.3 Error handling / retry strategy** — Define unified approach for data provider failures, backtest failures, QuestDB write failures, and frontend WebSocket reconnection. Huey supports `@huey.task(retries=2, retry_delay=30)` for task retries.
