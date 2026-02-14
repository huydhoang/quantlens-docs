## System Architecture Overview

```mermaid
flowchart TD
    subgraph Frontend["Frontend - Tanstack Start + React"]
        direction LR
        B["Strategy Editor<br/>Monaco · Templates · Linting"]
        C["Backtest Dashboard<br/>Config · Date Range · Assets"]
        D["Results Visualization<br/>Charts · Trade History · Equity"]
        E["Portfolio Analytics<br/>Sharpe · Drawdown · Stats"]
    end

    subgraph API["API Layer · Tanstack Start Routes"]
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
        L1[Finnhub Client]
        L2[Alpaca Client]
        L3[Redis Cache]
    end

    subgraph PG["PostgreSQL"]
        direction LR
        M1[Strategies]
        M2[Backtest Results]
        M3[User Data]
    end

    subgraph TSDB["TimeSeries DB"]
        direction LR
        N1[Market Data]
        N2[Tick Data]
    end

    Frontend -.->|HTTP / WebSocket| API
    API -.->|REST / gRPC| Nautilus
    API -.->|REST| DataProv
    Nautilus -.->|SQLAlchemy| PG
    DataProv -.->|Write| TSDB
    Nautilus -.->|Read| TSDB
```

## Frontend Component Architecture

```mermaid
graph LR
    subgraph "TanStack Start File Structure"
        A[src/routes] --> B[strategies.tsx]
        A --> C[backtest.$id.tsx]
        A --> D[api.strategies.ts]
        A --> E[api.backtest.ts]
        
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
    J -->|calls| D
    K -->|calls| E
    H -->|uses| J
    H1 -->|uses| K
```

## Monaco Editor Integration Flow

```mermaid
sequenceDiagram
    participant User
    participant React as React Component
    participant Monaco as Monaco Editor
    participant PyLinter as Python Linter
    participant TSStart as TanStack Start API
    participant Nautilus as NautilusTrader
    
    User->>React: Open Strategy Editor
    React->>Monaco: Initialize Editor
    Monaco->>Monaco: Load Python Language Mode
    
    alt New Strategy
        React->>TSStart: GET /api/strategies/template
        TSStart-->>React: Return template code
        React->>Monaco: Set Value
    else Existing Strategy
        React->>TSStart: GET /api/strategies/:id
        TSStart-->>React: Return strategy code
        React->>Monaco: Set Value
    end
    
    User->>Monaco: Type Code
    Monaco->>PyLinter: Request Validation
    PyLinter->>PyLinter: Syntax Check
    PyLinter-->>Monaco: Return Diagnostics
    
    User->>React: Click "Validate Strategy"
    React->>TSStart: POST /api/strategies/validate
    TSStart->>Nautilus: Dry-run parse
    Nautilus-->>TSStart: Validation Result
    TSStart-->>React: Success/Error
    
    User->>React: Save Strategy
    React->>TSStart: POST /api/strategies
    TSStart->>Nautilus: Register Strategy Class
    TSStart-->>React: Strategy ID
```

## Backtest Execution Flow

```mermaid
sequenceDiagram
    autonumber
    participant UI as TanStack UI
    participant API as TanStack API
    participant Queue as Task Queue<br/>BullMQ/Redis
    participant Worker as Backtest Worker
    participant Nautilus as NautilusTrader
    participant Data as Data Provider<br/>Finnhub/Alpaca
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
        F[Finnhub API] -->|WebSocket/REST| C[Data Ingestion Service]
        A[Alpaca API] -->|WebSocket/REST| C
        
        C -->|Raw Data| D[Data Normalizer]
        D -->|Standardized| E[(TimescaleDB<br/>OHLCV + Tick)]
        D -->|Cache| R[(Redis<br/>Hot Data)]
    end
    
    subgraph "Backtest Data Access"
        E -->|Historical| G[Nautilus Data Catalog]
        R -->|Real-time| G
        
        G -->|Backtest Venues| H[Nautilus Engine]
        H -->|Simulated Feeds| I[Strategy Execution]
    end
    
    subgraph "Live Data for UI"
        F -->|Quotes| J[TanStack Start API]
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
        +Engine engine
        +DataCatalog catalog
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

```mermaid
graph TB
    subgraph "Vercel/Edge"
        A[TanStack Start<br/>Frontend + API]
    end
    
    subgraph "AWS/GCP"
        B[ECS/K8s<br/>Backtest Workers]
        C[Redis<br/>Queue + Cache]
        
        D[RDS PostgreSQL<br/>Primary DB]
        E[TimescaleDB<br/>Market Data]
        
        F[S3<br/>Strategy Files<br/>Large Results]
    end
    
    subgraph "External APIs"
        G[Finnhub]
        H[Alpaca Markets]
    end
    
    A -->|Enqueue Jobs| C
    B -->|Consume| C
    B -->|Read/Write| D
    B -->|Read| E
    B -->|Fetch| G
    B -->|Fetch| H
    A -->|Query| D
    A -->|Query| E
```

## Key Implementation Recommendations

Based on this architecture, here are critical implementation points:

**1. Monaco Editor Setup**
- Use `@monaco-editor/react` with Python language support
- Implement custom completion providers for NautilusTrader APIs
- Add Python linting via WebAssembly (Pyodide) or backend validation endpoint

**2. NautilusTrader Integration**
- Run backtests in isolated worker processes (Docker containers recommended)
- Use NautilusTrader's `BacktestEngine` with custom `DataCatalog`
- Implement adapter pattern for Finnhub/Alpaca data normalization to Nautilus `QuoteTick`/`TradeTick`

**3. TanStack Start Patterns**
- Use server functions for strategy validation (compile Python without execution)
- Implement streaming for real-time backtest progress via `createEventStream`
- Leverage TanStack Query for optimistic UI updates on backtest submission

**4. Data Management**
- Use TimescaleDB for time-series market data (hypertables for performance)
- Implement data warming strategy - prefetch likely-needed data into Redis
- Cache Finnhub/Alpaca API responses to respect rate limits

**5. Security Considerations**
- Sandbox Python execution (restricted environment, no network access)
- Validate all strategy code before execution (AST parsing)
- Implement resource limits (max backtest duration, memory caps)
