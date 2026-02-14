# QuantLens — Architecture & Design Docs

QuantLens is a web-based backtesting platform that lets users write, test, and analyze quantitative trading strategies. It combines a **TanStack Start + React** frontend with a **Python/FastAPI** backend powered by **NautilusTrader**'s Rust core, providing institutional-grade simulation performance with an accessible browser-based workflow.

## Architecture at a Glance

```
TanStack Start (Frontend + API)
        │
        ├── Monaco Editor ──── Python strategy authoring
        ├── Backtest Dashboard ── Config, progress, results
        └── Portfolio Analytics ── Sharpe, drawdown, stats
        │
   API Layer (REST / WebSocket)
        │
   Python Backend (FastAPI / Granian)
        │
        ├── NautilusTrader ──── Rust core via PyO3
        ├── Celery Workers ──── Distributed backtest execution
        ├── Data Providers ──── Tiingo, Alpaca, Finnhub
        └── PyPortfolioOpt ──── Portfolio optimization
        │
   Storage
        ├── PostgreSQL ──────── Strategies, results, users
        ├── TimescaleDB ─────── OHLCV market data
        ├── Redis ───────────── Cache, task queue
        └── Parquet Catalog ─── Immutable validated datasets
```

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Server language** | Python | NautilusTrader's hot path is already Rust via PyO3; Python orchestrates the engine and provides access to the unmatched quant ecosystem |
| **Backtest engine** | NautilusTrader | Rust-powered event-driven engine with Python strategy API — institutional speed without sacrificing usability |
| **Primary data source** | Tiingo | Most generous free-tier limits (1,000 req/day, 30+ years EOD per call) for bulk historical data |
| **Time-series DB (Phase 1)** | TimescaleDB | Full PostgreSQL compatibility, mutable data for corrections, mature tooling — sufficient at free-tier scale |
| **Time-series DB (Phase 2+)** | QuestDB | Native `SAMPLE BY`, `ASOF JOIN`, 11M+ rows/sec ingestion for when scale demands it |
| **Frontend** | TanStack Start + React | Server functions, streaming backtest progress, Monaco Editor for strategy authoring |
| **Task queue** | Celery | Battle-tested reliability, canvas workflows for backtest pipelines, Redis broker (no new infrastructure), Flower monitoring |

## Documents

| Document | Description |
|----------|-------------|
| [system_design.md](system_design.md) | Full system architecture — frontend components, backtest execution flow, data flow, NautilusTrader integration, database schema, and deployment topology |
| [python_rust_or_go.md](python_rust_or_go.md) | Server language decision analysis — why Python wins given NautilusTrader's hybrid Rust/Python architecture, with ecosystem comparisons across Python, Rust, and Go |
| [data_providers.md](data_providers.md) | Multi-provider strategy for free-tier data — Tiingo, Alpaca, Finnhub, and Alpha Vantage compared on rate limits, data quality, and coverage, plus the validation pipeline |
| [ohlcv_database.md](ohlcv_database.md) | Time-series database evaluation — QuestDB vs TimescaleDB vs InfluxDB vs MongoDB for OHLCV storage, with a phased adoption plan |
| [task_queue.md](task_queue.md) | Task queue decision analysis — why Celery over Dramatiq, RQ, and Taskiq, with configuration for NautilusTrader backtest workers |

## Tech Stack

**Frontend:** TanStack Start, React, Monaco Editor, TanStack Query

**Backend:** Python, FastAPI, Granian (Rust ASGI), Celery

**Engine:** NautilusTrader (Rust core + Python bindings via PyO3)

**Data:** Tiingo (primary EOD), Alpaca (intraday/paper trading), Finnhub (fundamentals)

**Storage:** PostgreSQL, TimescaleDB, Redis, Apache Parquet

**Optimization:** PyPortfolioOpt, Polars
