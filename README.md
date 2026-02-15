# QuantLens — Architecture & Design Docs

QuantLens is a **local-first** alpha research, strategy backtesting, and portfolio optimization desktop application. It combines a **Tauri + Vite + React** frontend with a **Python/FastAPI** backend powered by **NautilusTrader**'s Rust core, providing institutional-grade simulation performance with an accessible desktop workflow. Tauri's lightweight Rust shell wraps a Vite-powered React SPA, with **TanStack Query** for REST data fetching and **TanStack Router** for type-safe routing.

The project will evolve into a **platform** where quants can submit their backtesting results and deploy strategies live to track and showcase real-world performance. The deployed platform app uses **Neon** (managed PostgreSQL) as its database.

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
   Python Backend (FastAPI / Uvicorn)
        │
        ├── NautilusTrader ──── Rust core via PyO3
        ├── Celery Workers ──── Distributed backtest execution
        ├── Data Providers ──── Tiingo, Alpaca, Finnhub
        └── skfolio ─────────── Portfolio optimization
        │
   Storage (all local Docker containers)
        ├── PostgreSQL ──────── Strategies, results, users
        ├── QuestDB ─────────── OHLCV market data (SAMPLE BY, ASOF JOIN, LATEST ON)
        ├── MongoDB ─────────── Fundamentals, economic indicators
        ├── Redis ───────────── Cache, task queue
        └── Parquet Catalog ─── Immutable validated datasets
```

### Future Platform App (Deployed)

```
React Platform App
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
| **Frontend** | Tauri + Vite + React | Desktop app with TanStack Query (REST caching), TanStack Router (type-safe routing), WebSocket streaming for backtest progress, Monaco Editor for strategy authoring |
| **NoSQL DB** | MongoDB (local Docker) | Flexible document model for semi-structured fundamentals and economic data; aggregation framework for screening queries |
| **Task queue** | Celery | Battle-tested reliability, canvas workflows for backtest pipelines, Redis broker (no new infrastructure), Flower monitoring |
| **Platform DB** | Neon (managed PostgreSQL) | Serverless PostgreSQL for the deployed platform app — user profiles, submitted results, live strategy tracking |
| **Deployment (local)** | Tauri desktop app + Docker Compose | Tauri app runs natively; backend services (FastAPI, databases, Redis, workers) start with `docker compose up` |
| **Deployment (platform)** | Cloud (TBD) | Future deployed React app for strategy showcasing and live performance tracking |

## Documents

| Document | Description |
|----------|-------------|
| [core_engine.md](core_engine.md) | Core engine decision analysis — why NautilusTrader alone (not a dual VectorBT + NautilusTrader stack), with ecosystem comparison, dual-engine trade-offs, and integration with QuantLens architecture |
| [local_frontend.md](local_frontend.md) | Local desktop frontend tech stack decision — why Tauri + Vite + React + TanStack over Electron, Next.js, TanStack Start, and Astro, with architecture diagram and implementation details |
| [system_design.md](system_design.md) | Full system architecture — frontend components, backtest execution flow, data flow, NautilusTrader integration, database schema, and deployment topology |
| [python_rust_or_go.md](python_rust_or_go.md) | Server language decision analysis — why Python wins given NautilusTrader's hybrid Rust/Python architecture, with ecosystem comparisons across Python, Rust, and Go |
| [data_providers.md](data_providers.md) | Multi-provider strategy for free-tier data — Tiingo, Alpaca, Finnhub, and Alpha Vantage compared on rate limits, data quality, and coverage, plus the validation pipeline |
| [ohlcv_database.md](ohlcv_database.md) | Time-series database evaluation — QuestDB vs TimescaleDB vs InfluxDB vs MongoDB for OHLCV storage, with rationale for QuestDB as the local-first default |
| [nosql_database.md](nosql_database.md) | NoSQL database evaluation — MongoDB vs DataStax Astra vs Cosmos DB vs Firestore for stock fundamentals and economic indicators, leveraging flexible schemas for semi-structured financial data |
| [task_queue.md](task_queue.md) | Task queue decision analysis — why Celery over Dramatiq, RQ, and Taskiq, with configuration for NautilusTrader backtest workers |
| [asgi_rsgi_wsgi.md](asgi_rsgi_wsgi.md) | Web interface decision analysis — why ASGI over WSGI/RSGI for NautilusTrader real-time streaming + skfolio optimization workloads |
| [asgi_web_server.md](asgi_web_server.md) | ASGI framework & architecture decision — FastAPI vs Starlette vs vanilla Granian, performance benchmarks, hybrid two-tier architecture for research and real-time trading |
| [vector_database.md](vector_database.md) | Vector database evaluation — LanceDB vs Qdrant vs Weaviate vs Milvus vs ChromaDB for local LLM chat, semantic search, and RAG pipelines, with embedded-first architecture for local deployment |

## Tech Stack

**Frontend:** Tauri, Vite, React, TanStack Query, TanStack Router, Monaco Editor

**Backend:** Python, FastAPI, Uvicorn (uvloop), Celery

**Engine:** NautilusTrader (Rust core + Python bindings via PyO3)

**Data:** Tiingo (primary EOD), Alpaca (intraday/paper trading), Finnhub (fundamentals)

**Storage (Local App):** PostgreSQL, QuestDB, MongoDB, Redis, Apache Parquet

**Storage (Platform App):** Neon (managed PostgreSQL)

**Optimization:** skfolio, Polars

**Infrastructure:** Docker Compose (local), Cloud TBD (platform)
