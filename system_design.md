## System Architecture Overview

QuantLens is a **local-first** desktop application for alpha research, strategy backtesting, and portfolio optimization — powered by a **Tauri** shell wrapping a **Vite + React** SPA, with backend services Dockerized for easy setup. A future **platform app** (deployed **TanStack Start + React** app on Neon) will allow quants to submit backtesting results and deploy strategies live to track and showcase real-world performance.

### Local App (Dockerized)

```mermaid
flowchart TD
    subgraph Frontend["Frontend — Tauri + Vite + React SPA"]
        direction LR
        B["Strategy Editor<br/>Monaco · Templates · Linting"]
        C["Backtest Dashboard<br/>Config · Date Range · Assets"]
        D["Results Visualization<br/>Charts · Trade History · Equity"]
        E["Portfolio Analytics<br/>Sharpe · Drawdown · Stats"]
    end

    subgraph API["API Layer · Gunicorn+Uvicorn · Raw ASGI"]
        direction LR
        G[Strategy Endpoints]
        H[Backtest Engine Proxy]
        I[Data Service Proxy]
        J[Results API]
    end

    subgraph Nautilus["NautilusTrader Engine"]
        direction LR
        K1[Strategy Executor]
        K2[Risk Manager]
        K3[Portfolio Manager]
        K4[Execution Simulator]
    end

    subgraph DataProv["Data Providers"]
        direction LR
        L0[Tiingo Client]
        L1[Finnhub Client]
        L2[Alpaca Client]
        L3[Redis Cache]
    end

    subgraph PG["PostgreSQL (Local Docker)"]
        direction LR
        M1[Strategies]
        M2[Backtest Results]
        M3[User Data]
    end

    subgraph TSDB["QuestDB (Local Docker)"]
        direction LR
        N1[Market Data]
        N2[Tick Data]
    end

    Frontend -.->|HTTP / WebSocket| API
    API -.->|Celery / Redis| Nautilus
    API -.->|REST| DataProv
    Nautilus -.->|psycopg / asyncpg| PG
    DataProv -.->|Write| TSDB
    Nautilus -.->|Read| TSDB
```

## Frontend Component Architecture

```mermaid
graph LR
    subgraph "Vite + React SPA File Structure (TanStack Router)"
        A[src/routes] --> B[strategies.tsx]
        A --> C[backtest.$id.tsx]
        
        F[components] --> G[MonacoStrategyEditor.tsx]
        F --> H[BacktestConfigForm.tsx]
        F --> H1[ResultsDashboard.tsx]
        F --> H2[EquityChart.tsx]
        
        I[hooks] --> J[useStrategy.ts]
        I --> K[useBacktest.ts]
        I --> L[useMarketData.ts]
        
        M[lib] --> N[nautilusClient.ts]
        M --> O[dataProviders.ts]
        M --> P[pythonLinter.ts]
    end
    
    G -->|uses| P
    J -->|calls| N
    K -->|calls| N
    H -->|uses| J
    H1 -->|uses| K
```

## Monaco Editor Integration Flow

```mermaid
sequenceDiagram
    participant User
    participant React as React Component
    participant Monaco as Monaco Editor
    participant PyLinter as Python Linter<br/>Pyodide WASM
    participant FastAPI as FastAPI Backend
    participant Nautilus as NautilusTrader
    
    User->>React: Open Strategy Editor
    React->>Monaco: Initialize Editor
    Monaco->>Monaco: Load Python Syntax Colorization
    
    alt New Strategy
        React->>FastAPI: GET /api/strategies/template
        FastAPI-->>React: Return template code
        React->>Monaco: Set Value
    else Existing Strategy
        React->>FastAPI: GET /api/strategies/:id
        FastAPI-->>React: Return strategy code
        React->>Monaco: Set Value
    end
    
    User->>Monaco: Type Code
    Monaco->>PyLinter: Debounced Syntax Check
    PyLinter->>PyLinter: AST Parse via Pyodide
    PyLinter-->>Monaco: Set Model Markers
    
    User->>React: Click "Validate Strategy"
    React->>FastAPI: POST /api/strategies/validate
    FastAPI->>Nautilus: Dry-run parse
    Nautilus-->>FastAPI: Validation Result
    FastAPI-->>React: Success/Error
    
    User->>React: Save Strategy
    React->>FastAPI: POST /api/strategies
    FastAPI->>Nautilus: Register Strategy Class
    FastAPI-->>React: Strategy ID
```

## Backtest Execution Flow

```mermaid
sequenceDiagram
    autonumber
    participant UI as React UI
    participant API as FastAPI
    participant Queue as Task Queue<br/>Celery/Redis
    participant Worker as Celery Worker<br/>prefork pool
    participant Nautilus as NautilusTrader<br/>BacktestEngine
    participant Data as Data Provider<br/>Tiingo/Alpaca/Finnhub
    participant DB as PostgreSQL
    
    UI->>API: POST /api/backtest/run
    Note over UI,API: {strategyId, params, dateRange, symbols}
    
    API->>DB: Create Backtest Job
    API->>Queue: Enqueue Job
    API-->>UI: Job ID (202 Accepted)
    
    UI->>API: WebSocket Connect /ws/backtest/:id
    
    Queue->>Worker: Pickup Job
    Worker->>Nautilus: Initialize Engine
    
    loop For each symbol
        Worker->>Data: Request Historical Data
        Data-->>Worker: OHLCV/Tick Data
        Worker->>Nautilus: Feed Data
    end
    
    Nautilus->>Nautilus: Run Simulation
    Nautilus-->>Worker: Progress Events
    
    loop Real-time Updates
        Worker->>Queue: Publish Progress
        Queue->>API: Broadcast
        API->>UI: WebSocket Message
    end
    
    Nautilus-->>Worker: Completed Results
    Worker->>DB: Store Results
    Worker->>Queue: Job Complete
    
    API->>UI: WebSocket: Complete
    UI->>API: GET /api/backtest/:id/results
    API->>DB: Fetch Results
    API-->>UI: Full Backtest Report
```

## Data Flow Architecture

```mermaid
graph TB
    subgraph "Market Data Ingestion"
        T[Tiingo API] -->|WebSocket/REST| C[Data Ingestion Service]
        F[Finnhub API] -->|WebSocket/REST| C
        A[Alpaca API] -->|WebSocket/REST| C
        
        C -->|Raw Data| D[Data Normalizer]
        D -->|Standardized| E[(QuestDB<br/>OHLCV + Tick)]
        D -->|Cache| R[(Redis<br/>Hot Data)]
    end
    
    subgraph "Backtest Data Access"
        E -->|Historical| G[Nautilus ParquetDataCatalog]
        R -->|Real-time| G
        
        G -->|Backtest Venues| H[Nautilus Engine]
        H -->|Simulated Feeds| I[Strategy Execution]
    end
    
    subgraph "Live Data for UI"
        T -->|IEX Quotes| J[FastAPI WebSocket]
        F -->|Quotes| J
        A -->|Quotes| J
        J -->|WebSocket| K[Market Data Hook]
        K -->|Real-time| L[Price Ticker Component]
    end
    
    subgraph "Strategy Data Requirements"
        M[Strategy Config] -->|Symbols| N[Data Requirements]
        N -->|Check| O[Data Availability Service]
        O -->|Query| E
        O -->|Cache Status| R
    end
```

## NautilusTrader Integration Detail

```mermaid
classDiagram
    class NautilusBacktestService {
        +BacktestEngine engine
        +ParquetDataCatalog catalog
        +StrategyFactory factory
        +run_backtest(config)
        +validate_strategy(code)
        +get_results()
    }
    
    class StrategyLoader {
        +load_from_string(python_code)
        +validate_syntax(code)
        +extract_parameters(code)
        +register_strategy(class_def)
    }
    
    class DataProviderAdapter {
        +TiingoAdapter tiingo
        +FinnhubAdapter finnhub
        +AlpacaAdapter alpaca
        +fetch_historical(symbols, start, end)
        +fetch_instruments()
        +normalize_to_nautilus(raw_data)
    }
    
    class BacktestConfig {
        +UUID strategy_id
        +List~str~ symbols
        +DateTime start_date
        +DateTime end_date
        +Dict parameters
        +Decimal initial_capital
    }
    
    class ResultProcessor {
        +calculate_metrics(trades)
        +generate_equity_curve(account)
        +create_report()
        +export_to_json()
    }
    
    NautilusBacktestService --> StrategyLoader
    NautilusBacktestService --> DataProviderAdapter
    NautilusBacktestService --> ResultProcessor
    NautilusBacktestService ..> BacktestConfig : uses
```

## Database Schema

```mermaid
erDiagram
    USERS ||--o{ STRATEGIES : creates
    USERS ||--o{ BACKTESTS : runs
    STRATEGIES ||--o{ BACKTESTS : used_in
    BACKTESTS ||--|| RESULTS : generates
    
    USERS {
        uuid id PK
        string email
        string name
        timestamp created_at
    }
    
    STRATEGIES {
        uuid id PK
        uuid user_id FK
        string name
        text python_code
        jsonb parameters_schema
        boolean is_validated
        timestamp created_at
        timestamp updated_at
    }
    
    BACKTESTS {
        uuid id PK
        uuid strategy_id FK
        uuid user_id FK
        jsonb configuration
        string status
        decimal progress
        timestamp started_at
        timestamp completed_at
        string error_message
    }
    
    RESULTS {
        uuid backtest_id PK
        jsonb trades
        jsonb equity_curve
        jsonb metrics
        decimal total_return
        decimal sharpe_ratio
        decimal max_drawdown
        integer total_trades
        jsonb monthly_returns
    }
    
    MARKET_DATA {
        composite symbol_timestamp PK
        string symbol
        timestamp timestamp
        decimal open
        decimal high
        decimal low
        decimal close
        bigint volume
        string source
    }
```

## Deployment Architecture

### Local App (Docker Compose + Embedded)

All services run locally — Docker containers via `docker compose up`, plus embedded databases (DuckDB, LanceDB) in the Python process:

```mermaid
graph TB
    subgraph "Docker Compose (Local)"
        A[Tauri Desktop App<br/>Vite + React SPA]
        B[Celery Workers<br/>Backtest Engine]
        C[Redis<br/>Queue + Cache]

        D[PostgreSQL<br/>Strategies · Results · Users]
        E[QuestDB<br/>OHLCV Market Data]
    end

    subgraph "Embedded (In-Process)"
        F[DuckDB<br/>Fundamentals · Economic Indicators]
        L[LanceDB<br/>Expert Analyses · Company News · RAG]
    end

    subgraph "External APIs"
        G[Finnhub]
        H[Alpaca Markets]
        TI[Tiingo]
    end

    A -->|Enqueue Jobs| C
    B -->|Consume| C
    B -->|Read/Write| D
    B -->|Read| E
    B -->|Read/Write| F
    B -->|Read| L
    B -->|Fetch| G
    B -->|Fetch| H
    B -->|Fetch| TI
    A -->|Query| D
    A -->|Query| E
```

### Future Platform App (Deployed)

The project will evolve into a platform where quants submit backtesting results and deploy strategies live to track and showcase real-world performance.

```mermaid
graph TB
    subgraph "Cloud (Deployed Platform)"
        PA[TanStack Start + React<br/>Strategy Showcase · Leaderboards]
        NeonDB[Neon PostgreSQL<br/>User Profiles · Submitted Results<br/>Live Strategy Tracking]
    end

    subgraph "Local (Quant's Machine)"
        LA[QuantLens Local App<br/>Docker Compose]
    end

    LA -->|Submit Results · Deploy Strategy| PA
    PA -->|Read/Write| NeonDB
```

## Key Implementation Recommendations

Based on this architecture, here are critical implementation points:

**1. Monaco Editor Setup**
- Use `@monaco-editor/react` — Python gets **syntax colorization only** (no built-in IntelliSense or validation; Monaco's `onValidate` fires only for JS/TS/CSS/JSON/HTML)
- Implement custom `CompletionItemProvider` for NautilusTrader APIs via `monaco.languages.registerCompletionItemProvider`
- Add Python linting via Pyodide (WASM) for client-side AST parsing, with markers set via `monaco.editor.setModelMarkers`; deep validation (NautilusTrader import resolution) must happen server-side

**2. NautilusTrader Integration**
- Run backtests in isolated worker processes — **one `BacktestNode` per process** (NautilusTrader enforces this due to global singleton state: force-stop flag, logger mode, Tokio runtime). Celery `prefork` pool satisfies this naturally.
- Use `BacktestEngine` (low-level, fine-grained control) or `BacktestNode` with `BacktestRunConfig` objects (high-level, recommended for production). The catalog class is `ParquetDataCatalog`, not `DataCatalog`.
- NautilusTrader is a **library, not a service** — there is no REST/gRPC API. The API layer enqueues jobs to Celery; workers import and call `nautilus_trader` directly in-process.
- Implement adapter pattern for Tiingo/Finnhub/Alpaca data normalization to Nautilus `Bar`/`QuoteTick`/`TradeTick` types

**3. Tauri + Vite + React Patterns**
- Use TanStack Router with file-based routing for type-safe navigation
- Use TanStack Query for REST data fetching with automatic caching and invalidation
- WebSocket connections are managed directly in React — no framework abstraction needed. Push real-time updates into TanStack Query cache via `queryClient.setQueryData()` for unified state management.
- See [local_frontend.md](local_frontend.md) for the full tech stack decision and architecture

**4. Data Management**
- Use QuestDB for time-series market data — native `SAMPLE BY` for OHLCV bar generation, `ASOF JOIN` for trade/quote correlation, and `LATEST ON` for efficient last-value-per-symbol queries. Running locally in Docker eliminates the free-tier constraints that previously favored TimescaleDB, and QuestDB's append-only columnar architecture with 11M+ rows/sec ingestion is purpose-built for financial market data.
- Implement data warming strategy — prefetch likely-needed data into Redis
- Cache API responses to respect rate limits: Tiingo limits are plan-dependent (hourly requests + daily requests + monthly bandwidth — see [pricing page](https://www.tiingo.com/pricing)); Finnhub free tier: 60 calls/min with a hard 30 calls/sec cap; Alpaca free tier: ~200 calls/min
- **Finnhub OHLCV caveat:** Stock Candles and Tick Data endpoints are **Premium-only**. On the free tier, Finnhub is useful for fundamentals, news, quotes, and recommendation data — not historical OHLCV ingestion. Tiingo (EOD + IEX intraday) and Alpaca should be the primary free-tier sources for historical price data.
- Tiingo provides **WebSocket streams** for IEX (US equities intraday), Crypto, and Forex — not just REST. Use these for real-time data instead of polling.
- Store validated datasets in Parquet format for NautilusTrader's `ParquetDataCatalog` — this is the primary backtest data path, not live API calls
- For OHLCV data corrections, use QuestDB's insert-new-row pattern (append a corrected row with a later processing timestamp) rather than in-place updates. The `LATEST ON` clause efficiently retrieves the most recent version per symbol.

**5. Security Considerations**
- Sandbox Python execution (restricted environment, no network access)
- Validate all strategy code before execution (AST parsing)
- Implement resource limits (max backtest duration, memory caps)

**6. Portfolio Optimization (skfolio)**
- Use `skfolio` as the optimization engine with a scikit-learn-native `fit/predict/transform` workflow for clean FastAPI dependency injection.
- Default to downside-aware risk objectives (`CVaR`) and monitor drawdown-focused metrics (max drawdown, conditional drawdown, tracking error).
- Use walk-forward cross-validation for optimization endpoints to reduce overfitting in production allocation workflows.
