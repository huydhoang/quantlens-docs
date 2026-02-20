# QuantLens — Architecture & Design Docs

QuantLens is a **local-first** alpha research, strategy backtesting, and portfolio optimization desktop application. It combines a **Tauri + Vite + React** frontend with a **Python/FastAPI** backend powered by **NautilusTrader**'s Rust core, providing institutional-grade simulation performance with an accessible desktop workflow. Tauri's lightweight Rust shell wraps a Vite-powered React SPA, with **TanStack Query** for REST data fetching and **TanStack Router** for type-safe routing.

The project will evolve into a **platform** where quants can submit their backtesting results and deploy strategies live to track and showcase real-world performance. The deployed platform app uses **TanStack Start + React** for the frontend and **Neon** (managed PostgreSQL) as its database.

## Architecture at a Glance

### Local App (Dockerized)

```
Tauri Desktop App (Vite + React SPA)
        │
        ├── Monaco Editor ──── Python strategy authoring
        ├── Backtest Dashboard ── Config, progress, results
        └── Portfolio Analytics ── Sharpe, drawdown, stats
        │
   API Layer (REST / WebSocket)
        │
   Python Backend (Gunicorn+Uvicorn · Raw ASGI)
        │
        ├── NautilusTrader ──── Rust core via PyO3
        ├── Celery Workers ──── Distributed backtest execution
        ├── Data Providers ──── Tiingo, Alpaca, Finnhub
        └── skfolio ─────────── Portfolio optimization
        │
   Storage (all local, embedded or Docker containers)
        ├── PostgreSQL ──────── Strategies, results, users
        ├── QuestDB ─────────── OHLCV market data (SAMPLE BY, ASOF JOIN, LATEST ON)
        ├── DuckDB ──────────── Fundamentals, economic indicators (embedded, zero-config)
        ├── Redis ───────────── Cache, task queue
        └── Parquet Catalog ─── Immutable validated datasets
```

### Future Platform App (Deployed)

```
TanStack Start + React (Frontend + API)
        │
        ├── Strategy Showcase ── Leaderboards, performance tracking
        ├── Live Deployment ──── Real-world strategy monitoring
        └── Quant Profiles ───── Portfolio showcases
        │
   Neon PostgreSQL ──────────── User profiles, submitted results, live tracking
        │
   QuantLens Local App ─────── Submit results, deploy strategies
```

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Architecture** | Local-first, Dockerized | Single `docker compose up` starts all services — no cloud accounts or managed services required for core functionality; Dockerized for consistent environments |
| **Server language** | Python | NautilusTrader's hot path is already Rust via PyO3; Python orchestrates the engine and provides access to the unmatched quant ecosystem |
| **Backtest engine** | NautilusTrader | Rust-powered event-driven engine with Python strategy API — institutional speed without sacrificing usability |
| **Primary data source** | Tiingo | Most generous free-tier limits (1,000 req/day, 30+ years EOD per call) for bulk historical data |
| **Time-series DB** | QuestDB | Native `SAMPLE BY`, `ASOF JOIN`, `LATEST ON`, 11M+ rows/sec ingestion — purpose-built for financial market data. Running locally in Docker removes the free-tier constraints that previously favored TimescaleDB |
| **Frontend (local)** | Tauri + Vite + React | Desktop app with TanStack Query (REST caching), TanStack Router (type-safe routing), WebSocket streaming for backtest progress, Monaco Editor for strategy authoring |
| **Frontend (platform)** | TanStack Start + React | Server functions, SSR for SEO, streaming, TanStack Query for data fetching |
| **Fundamentals DB** | DuckDB (embedded) | Benchmark winner: 34ms screening, 96ms complex queries. Top alternatives: SQLite (35ms, zero-config), PostgreSQL (38ms, already in stack), ClickHouse (45ms screening / 185ms complex). See [fundamentals_database.md](fundamentals_database.md). |
| **Task queue** | Celery | Battle-tested reliability, canvas workflows for backtest pipelines, Redis broker (no new infrastructure), Flower monitoring |
| **Platform DB** | Neon (managed PostgreSQL) | Serverless PostgreSQL for the deployed platform app — user profiles, submitted results, live strategy tracking |
| **Deployment (local)** | Tauri desktop app + Docker Compose | Tauri app runs natively; backend services (FastAPI, databases, Redis, workers) start with `docker compose up` |
| **Deployment (platform)** | Cloud (TBD) | Future deployed TanStack Start + React app for strategy showcasing and live performance tracking |

## Documents

| Document | Description |
|----------|-------------|
| [core_engine.md](core_engine.md) | Core engine decision analysis — why NautilusTrader alone (not a dual VectorBT + NautilusTrader stack), with ecosystem comparison, dual-engine trade-offs, and integration with QuantLens architecture |
| [local_frontend.md](local_frontend.md) | Local desktop frontend tech stack decision — why Tauri + Vite + React + TanStack over Electron, Next.js, TanStack Start, and Astro, with architecture diagram and implementation details |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Full system architecture — frontend components, backtest execution flow, data flow, NautilusTrader integration, database schema, and deployment topology |
| [python_rust_or_go.md](python_rust_or_go.md) | Server language decision analysis — why Python wins given NautilusTrader's hybrid Rust/Python architecture, with ecosystem comparisons across Python, Rust, and Go |
| [data_providers.md](data_providers.md) | Multi-provider strategy for free-tier data — Tiingo, Alpaca, Finnhub, and Alpha Vantage compared on rate limits, data quality, and coverage, plus the validation pipeline |
| [ohlcv_database.md](ohlcv_database.md) | Time-series database evaluation — QuestDB vs TimescaleDB vs InfluxDB vs MongoDB for OHLCV storage, with rationale for QuestDB as the local-first default |
| [fundamentals_database.md](fundamentals_database.md) | Fundamentals database evaluation — DuckDB as embedded columnar database for stock fundamentals and economic indicators, with full benchmark results (13 databases). Top alternatives: SQLite, PostgreSQL, ClickHouse. |
| [task_queue.md](task_queue.md) | Task queue decision analysis — why Celery over Dramatiq, RQ, and Taskiq, with configuration for NautilusTrader backtest workers |
| [asgi_rsgi_wsgi.md](asgi_rsgi_wsgi.md) | Web interface decision analysis — why ASGI over WSGI/RSGI for NautilusTrader real-time streaming + skfolio optimization workloads |
| [asgi_web_server.md](asgi_web_server.md) | ASGI web server architecture decision — Gunicorn+Uvicorn Raw ASGI by default, with FastAPI on Gunicorn+Uvicorn only when WebSocket support is required; extended benchmark results comparing all six stacks |
| [vector_database.md](vector_database.md) | Vector database evaluation — LanceDB vs Qdrant vs Weaviate vs Milvus vs ChromaDB for local LLM chat, semantic search, expert analyses, and RAG pipelines, with hybrid DuckDB + LanceDB architecture for LLM-powered financial analysis |

## Tech Stack

**Frontend (Local App):** Tauri, Vite, React, TanStack Query, TanStack Router, Monaco Editor

**Frontend (Platform App):** TanStack Start, React, TanStack Query

**Backend:** Python, Gunicorn+Uvicorn (uvloop), Celery

**Engine:** NautilusTrader (Rust core + Python bindings via PyO3)

**Data:** Tiingo (primary EOD), Alpaca (intraday/paper trading), Finnhub (fundamentals)

**Storage (Local App):** PostgreSQL, QuestDB, DuckDB, LanceDB, Redis, Apache Parquet

**Storage (Platform App):** Neon (managed PostgreSQL)

**Optimization:** skfolio, Polars

**Infrastructure:** Docker Compose (local), Cloud TBD (platform)
