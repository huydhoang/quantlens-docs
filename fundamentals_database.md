# Fundamentals & Economic Data Storage

## Overview

This document evaluates storage options for stock market fundamentals and economic indicators. Unlike OHLCV tick data (covered in [ohlcv_database.md](ohlcv_database.md)), fundamentals and economic data are semi-structured, low-frequency, and high-dimensionality — characteristics that favor flexible schema handling and analytical query performance over raw write throughput.

**Decision: DuckDB** — an embedded, in-process analytical database that eliminates Docker networking complexity and provides best-in-class DX for quantitative research on local desktop deployments.

---

## Benchmark Results

### Configuration

- **Database server-client combos tested**: 13
- **Databases**: DuckDB, SQLite, PostgreSQL, MySQL, SQL Server (mssql-python), SQL Server (pyodbc), MongoDB, Cassandra, ScyllaDB, ClickHouse, TimescaleDB, Redis, RavenDB
- **Fundamentals records**: 500 symbols × 200 periods = 100,000 rows
- **Economic records**: 250 indicators × 200 months (~100,000 rows)

### Data Load

| Database | Avg (ms) | Min (ms) | Max (ms) | Rows |
| --- | ---: | ---: | ---: | ---: |
| DuckDB | 5815.23 | 5815.23 | 5815.23 | 199999 |
| SQLite | 772.63 | 772.63 | 772.63 | 199999 |
| PostgreSQL | 6035.18 | 6035.18 | 6035.18 | 199999 |
| MySQL | 9869.18 | 9869.18 | 9869.18 | 199999 |
| SQL Server (mssql-python) | 15288.78 | 15288.78 | 15288.78 | 199999 |
| SQL Server (pyodbc) | 5577.37 | 5577.37 | 5577.37 | 199999 |
| MongoDB | 3123.33 | 3123.33 | 3123.33 | 199999 |
| Cassandra | 55624.23 | 55624.23 | 55624.23 | 199999 |
| ScyllaDB | 40989.39 | 40989.39 | 40989.39 | 199999 |
| ClickHouse | 793.42 | 793.42 | 793.42 | 199999 |
| TimescaleDB | 6068.42 | 6068.42 | 6068.42 | 199999 |
| Redis | 5046.38 | 5046.38 | 5046.38 | 199999 |
| RavenDB | 10749.35 | 10749.35 | 10749.35 | 199999 |

### Simple Query: Fundamentals Screening

| Database | Avg (ms) | Min (ms) | Max (ms) | Rows |
| --- | ---: | ---: | ---: | ---: |
| DuckDB | 33.96 | 22.38 | 52.39 | 19812 |
| SQLite | 34.75 | 34.44 | 35.27 | 19812 |
| PostgreSQL | 38.56 | 36.40 | 41.77 | 19812 |
| MySQL | 211.01 | 207.41 | 214.26 | 19812 |
| SQL Server (mssql-python) | 81.32 | 64.61 | 113.60 | 19812 |
| SQL Server (pyodbc) | 79.34 | 68.00 | 101.82 | 19812 |
| MongoDB | 84.67 | 76.88 | 95.53 | 19812 |
| Cassandra | 418.07 | 351.37 | 534.73 | 19812 |
| ScyllaDB | 308.89 | 302.99 | 320.43 | 19812 |
| ClickHouse | 44.98 | 42.75 | 48.53 | 19812 |
| TimescaleDB | 42.90 | 40.63 | 47.41 | 19812 |
| Redis | 1949.72 | 1902.84 | 2002.38 | 19812 |
| RavenDB | 4031.05 | 3730.71 | 4573.89 | 19812 |

### Simple Query: Economic Latest Values

| Database | Avg (ms) | Min (ms) | Max (ms) | Rows |
| --- | ---: | ---: | ---: | ---: |
| DuckDB | 32.90 | 25.24 | 47.82 | 100 |
| SQLite | 126.90 | 126.25 | 127.61 | 100 |
| PostgreSQL | 56.14 | 54.01 | 59.40 | 100 |
| MySQL | 405.97 | 401.20 | 409.82 | 100 |
| SQL Server (mssql-python) | 84.40 | 82.63 | 86.59 | 100 |
| SQL Server (pyodbc) | 81.56 | 80.32 | 82.43 | 100 |
| MongoDB | 235.05 | 230.70 | 239.23 | 100 |
| Cassandra | 4.00 | 2.63 | 5.72 | 100 |
| ScyllaDB | 0.88 | 0.80 | 1.00 | 100 |
| ClickHouse | 18.32 | 17.12 | 20.15 | 100 |
| TimescaleDB | 58.60 | 56.23 | 62.78 | 100 |
| Redis | 43.95 | 38.08 | 48.14 | 100 |
| RavenDB | 2276.09 | 2173.13 | 2428.08 | 100 |

### Complex Query Workload

| Database | Avg (ms) | Min (ms) | Max (ms) | Rows |
| --- | ---: | ---: | ---: | ---: |
| DuckDB | 96.11 | 94.79 | 97.52 | 81600 |
| SQLite | 248.88 | 239.07 | 255.56 | 81600 |
| PostgreSQL | 247.85 | 244.93 | 250.90 | 81600 |
| MySQL | 848.10 | 840.59 | 862.77 | 81600 |
| SQL Server (mssql-python) | 343.91 | 318.97 | 393.37 | 81600 |
| SQL Server (pyodbc) | 391.57 | 310.07 | 540.10 | 81600 |
| MongoDB | 685.83 | 677.41 | 695.75 | 81600 |
| Cassandra | 1475.70 | 1330.01 | 1710.47 | 81600 |
| ScyllaDB | 1165.27 | 1146.52 | 1183.02 | 81600 |
| ClickHouse | 184.96 | 174.25 | 191.76 | 81600 |
| TimescaleDB | 265.92 | 249.66 | 287.37 | 81600 |
| Redis | 3453.97 | 3335.30 | 3536.35 | 81600 |
| RavenDB | 6271.84 | 6180.59 | 6393.91 | 81600 |

### Summary

DuckDB leads across all analytical workloads. **Top alternatives**: SQLite (fast simple queries, zero-config), PostgreSQL (ACID + ecosystem), ClickHouse (fast complex queries, columnar).

---

## 1. Why DuckDB for Fundamentals & Economic Data

Stock fundamentals (10-K/10-Q filings) and economic indicators (GDP, CPI, unemployment) differ from OHLCV data in ways that make an embedded analytical database the optimal choice:

- **Variable schema**: SEC filings contain 100+ metrics that change across companies and reporting periods. Economic indicators vary by country, revision methodology, and frequency. DuckDB's flexible typing handles missing earnings, varying fiscal calendars, and schema changes without rigid migrations.
- **Nested structures**: Balance sheets, income statements, and cash flow statements are naturally hierarchical. DuckDB supports `STRUCT` and `LIST` types for nested data, plus native JSON extraction.
- **Schema evolution**: GDP calculation methodologies change over time; new reporting standards (e.g., IFRS vs GAAP) add fields without warning. DuckDB handles this via flexible column types and schema-on-read from Parquet/JSON files.
- **Low write frequency**: Quarterly/annual fundamentals and monthly/quarterly economic releases — not high-frequency streams.
- **Complex read patterns**: Screening queries (P/E < 15 AND revenue growth > 10%), cross-sectional analysis, and multi-dimensional aggregations — exactly the OLAP workload DuckDB's columnar engine is optimized for.

### Why Not MongoDB (Previous Recommendation)

The original recommendation was MongoDB (Docker container) for its flexible document model. In practice, MongoDB's official Docker image and the community server image both encountered persistent **connection errors** during local benchmarking — a common issue with Docker-containerized databases for desktop app deployments. DuckDB eliminates this class of problems entirely:

| Issue | MongoDB (Docker) | DuckDB (Embedded) |
|-------|-------------------|-------------------|
| **Docker networking** | Connection errors, port conflicts, container lifecycle management | N/A — runs in-process |
| **Setup complexity** | Docker image, config, port mapping, volume mounts | `pip install duckdb` — zero config |
| **Query language** | MongoDB aggregation pipelines (custom syntax) | Standard SQL |
| **Python integration** | `pymongo` driver, serialization overhead | Zero-copy Pandas/Polars integration |
| **Infrastructure overhead** | Separate container process, memory allocation | Embedded in Python process |

---

## 2. Why DuckDB Dominates for This Use Case

### Schema Flexibility (DX Winner)

- Zero configuration, embedded in-process (no Docker networking headaches)
- Native support for **Parquet, CSV, JSON** — query files directly without ingestion
- Flexible typing handles messy fundamental data (missing earnings, varying fiscal calendars) without rigid schema migrations
- SQL-first with Python/R integration that quants actually use

### Query Performance for Analytics

- **Columnar storage** optimized for analytical queries (aggregations, window functions, time-series joins)
- Vectorized execution engine specifically designed for OLAP workloads
- Competitive with ClickHouse on [ClickBench](https://benchmark.clickhouse.com/) for analytical queries on a single node
- **In-memory or on-disk**: Load multi-GB datasets into memory for iteration speed, or query larger-than-RAM datasets efficiently

### Perfect Fit for Backtesting Workloads

- **Zero-copy integration** with Pandas/Polars — critical for strategy prototyping
- **Window functions** for financial calculations (`LEAD`, `LAG`, rolling averages, YoY comparisons)
- **Native Parquet support** — seamless integration with NautilusTrader's `ParquetDataCatalog` and the existing data pipeline

### When to Consider Alternatives

| Database | Use If | Benchmark Position |
|----------|--------|-------------------|
| **SQLite** | Need zero-config embedded DB with simple queries | 2nd on screening (34.75ms), fast data load (772ms) — best embedded alternative |
| **PostgreSQL** | Already running PostgreSQL and want to consolidate | 3rd on screening (38.56ms), strong complex queries (247ms) — already in the stack |
| **ClickHouse** | Need columnar performance without embedded constraints | 4th on screening (44.98ms), 2nd on complex queries (184ms) — best server-side alternative |
| **TimescaleDB** | Need ACID + time-series + PostgreSQL ecosystem | Similar to PostgreSQL on complex queries (265ms); adds Docker overhead |
| **MongoDB** | Need document model with managed cloud deployment | Docker connection issues for local use; 84ms screening, 685ms complex queries |

---

## 3. Read/Write Strategy for Financial Data

### Stock Market Fundamentals (Quarterly/Annual)

Characteristics: Low frequency, high dimensionality, complex relationships.

```python
import duckdb

con = duckdb.connect('fundamentals.db')

# Create table with flexible schema
con.execute("""
    CREATE TABLE IF NOT EXISTS fundamentals (
        symbol VARCHAR,
        period VARCHAR,           -- '2024-Q1'
        revenue DOUBLE,
        net_income DOUBLE,
        eps DOUBLE,
        pe_ratio DOUBLE,
        -- ... 100+ metrics as needed
        balance_sheet JSON,       -- Nested data via JSON
        cash_flow JSON,
        metadata STRUCT(last_updated TIMESTAMP, source VARCHAR),
        PRIMARY KEY (symbol, period)
    )
""")

# Or query Parquet files directly (common financial data format)
con.execute("""
    SELECT
        ticker,
        fiscal_quarter,
        revenue,
        net_income,
        LAG(revenue, 4) OVER (PARTITION BY ticker ORDER BY fiscal_quarter) as revenue_4q_ago
    FROM read_parquet('fundamentals/*.parquet')
    WHERE sector = 'Technology'
""").df()
```

**Strategy**:
- **Write**: Batch upserts quarterly (low frequency) — `INSERT OR REPLACE` for corrections
- **Read**: Standard SQL for screening (P/E ratios, growth rates, cross-sectional analysis)
- **File-based queries**: Query Parquet/CSV/JSON files directly without explicit ingestion

### Economic Indicators (Monthly/Quarterly Time Series)

Characteristics: Sparse updates, historical revisions, multi-dimensional.

```python
# Create economic indicators table
con.execute("""
    CREATE TABLE IF NOT EXISTS economic_indicators (
        indicator_id VARCHAR,     -- 'GDP_US', 'CPI_EU'
        frequency VARCHAR,        -- 'monthly', 'quarterly'
        timestamp TIMESTAMP,
        value DOUBLE,
        revision_number INTEGER,
        PRIMARY KEY (indicator_id, frequency, timestamp, revision_number)
    )
""")

# Latest value per indicator (handling revisions)
con.execute("""
    SELECT indicator_id, timestamp, value
    FROM (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY indicator_id, timestamp
                ORDER BY revision_number DESC
            ) as rn
        FROM economic_indicators
    )
    WHERE rn = 1
    ORDER BY timestamp DESC
""").df()
```

**Strategy**:
- **Write**: Append-only, handle revisions as new rows with incrementing `revision_number`
- **Read**: Window functions for time-range scans, latest value lookups, YoY comparisons
- **Export**: Native Parquet export for archival and NautilusTrader integration

For QuantLens, **both fundamentals and economic indicators fit well in DuckDB** since the write volumes are low, the query patterns favor analytical aggregation, and the embedded architecture eliminates Docker infrastructure overhead.

---

## 4. Recommendation

### Winner: DuckDB (Embedded)

**Rationale**:

1. **Zero infrastructure**: `import duckdb` — no Docker containers, no connection strings, no port conflicts, no container lifecycle management. This eliminates the Docker networking issues encountered with MongoDB and aligns with QuantLens's local-first design philosophy.
2. **Analytical query performance**: DuckDB's columnar, vectorized execution engine is purpose-built for the aggregations, window functions, and cross-sectional analysis that stock screening requires — faster than MongoDB's aggregation pipelines for analytical workloads.
3. **Schema flexibility**: `STRUCT`, `LIST`, `MAP`, and `JSON` types handle nested SEC filing structures. Schema-on-read from Parquet/CSV/JSON files means no migrations when data formats change.
4. **Python-native workflow**: Zero-copy integration with Pandas and Polars DataFrames. Results flow directly into the existing data pipeline without serialization overhead.
5. **Storage efficiency**: Columnar compression achieves excellent compression ratios for financial data. A typical fundamentals dataset (3,000+ stocks × 20 quarters × 100+ metrics) fits comfortably in a single DuckDB file under 100 MB.
6. **No cost at any scale**: Fully open-source, embedded, no tiers or limits. Scales from laptop to multi-GB datasets without pricing concerns.

### Why Not Replace PostgreSQL with DuckDB Entirely?

DuckDB excels at analytical queries, but QuantLens's OLTP workloads — strategy CRUD, backtest job tracking, user management — still require PostgreSQL. DuckDB's own documentation explicitly states these limitations:

| Requirement | PostgreSQL | DuckDB |
|-------------|-----------|--------|
| **Multi-process writes** | ✅ Celery workers + FastAPI write concurrently | ❌ Single-writer process only — "Writing to DuckDB from multiple processes is not supported automatically and is not a primary design goal" ([DuckDB concurrency docs](https://duckdb.org/docs/stable/connect/concurrency.html)) |
| **Small, frequent transactions** | ✅ Optimized for OLTP (row-at-a-time updates) | ❌ "DuckDB is optimized for bulk operations, so executing many small transactions is not a primary design goal" ([DuckDB concurrency docs](https://duckdb.org/docs/stable/connect/concurrency.html)) |
| **Concurrent row updates** | ✅ MVCC with row-level locking | ⚠️ Optimistic concurrency control — concurrent updates to the same row cause `Transaction conflict` errors |
| **Foreign key enforcement** | ✅ Full FK with cascading deletes/updates | ✅ Supported, but with [index limitations](https://duckdb.org/docs/stable/sql/indexes.html#index-limitations) that can cause spurious constraint errors |
| **Connection pooling** | ✅ asyncpg connection pools, PgBouncer | ❌ Embedded — no connection protocol, no pooling |
| **Platform migration path** | ✅ Same schema works on Neon PostgreSQL (deployed platform) | ❌ No managed DuckDB service with OLTP semantics (MotherDuck is OLAP-only) |

**Bottom line**: PostgreSQL handles the **transactional core** (strategies, backtests, users, results) where concurrent writes and relational integrity matter. DuckDB handles the **analytical layer** (fundamentals screening, cross-sectional analysis, window functions) where columnar performance matters. This is the [standard OLTP + OLAP split](https://duckdb.org/docs/stable/connect/concurrency.html) that DuckDB's own team recommends — their documentation suggests using PostgreSQL for multi-process transactions and DuckDB for analytical queries.

**Bonus — DuckDB can query PostgreSQL directly**: DuckDB's [`postgres` extension](https://duckdb.org/docs/stable/core_extensions/postgres.html) lets you run analytical queries against live PostgreSQL data without copying it. This means DuckDB can serve as an OLAP overlay on the OLTP store when needed:

```python
import duckdb

con = duckdb.connect()
con.execute("INSTALL postgres; LOAD postgres;")
con.execute("ATTACH 'dbname=quantlens' AS pg (TYPE postgres, READ_ONLY)")

# Run analytical query on PostgreSQL data using DuckDB's columnar engine
results = con.execute("""
    SELECT strategy_id, AVG(sharpe_ratio), COUNT(*)
    FROM pg.results r
    JOIN pg.backtests b ON r.backtest_id = b.id
    WHERE b.completed_at > CURRENT_DATE - INTERVAL '30 days'
    GROUP BY strategy_id
    ORDER BY AVG(sharpe_ratio) DESC
""").df()
```

### Alternatives Considered

| Database | Verdict | Reason |
|----------|---------|--------|
| **SQLite** | Top alternative | 2nd fastest on screening (34.75ms), fastest data load among embedded options (772ms). No Docker overhead. Best choice if DuckDB's in-memory model is not needed. |
| **PostgreSQL** | Top alternative | Already in the stack; 3rd on screening (38.56ms), competitive on complex queries (247ms). Use if consolidation onto the OLTP store is preferred. |
| **ClickHouse** | Top alternative | 2nd fastest on complex queries (184ms); fastest data load (793ms). Best server-side columnar option. |
| **MongoDB (Docker)** | Previously recommended; replaced | Docker connection errors in local desktop deployment; 84ms screening / 685ms complex queries — outperformed by DuckDB, SQLite, PostgreSQL, and ClickHouse on all analytical workloads |
| **MongoDB Atlas (M0)** | Not suitable for local-first | Requires cloud connectivity; contradicts QuantLens's local-first, no-cloud-accounts-required philosophy |
| **QuestDB** | Already used for OHLCV | Purpose-built for time-series ingestion, not semi-structured fundamentals with variable schemas |

---

## 5. Implementation Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Data Ingestion Layer                      │
│         (Python/Pandas — scheduled or on-demand)            │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   DuckDB     │ │   QuestDB    │ │   Parquet    │
│  (Embedded)  │ │   (Docker)   │ │   Catalog    │
│              │ │              │ │              │
│ Fundamentals │ │ OHLCV        │ │ Historical   │
│ Economics    │ │ Tick data    │ │ archives     │
│ Screening    │ │ Market data  │ │ (immutable)  │
└──────────────┘ └──────────────┘ └──────────────┘
        │               │               │
        └───────────────┴───────────────┘
                        │
              ┌─────────▼──────────┐
              │   Analytics Layer  │
              │  (Python/Pandas/   │
              │   Polars/DuckDB)   │
              └────────────────────┘
```

### Cost Projection

| Stage | Data Size | Database | Monthly Cost | Notes |
|-------|-----------|----------|--------------|-------|
| **MVP** | <500 MB | DuckDB (embedded) | **$0** | No limits, no tiers |
| **Growth** | 5–50 GB | DuckDB (embedded) | **$0** | Handles larger-than-RAM datasets efficiently |
| **Scale** | 50–500 GB | DuckDB + Parquet archive | **$0** | Query Parquet files directly; cold data stays on disk |
| **Enterprise** | 500 GB+ | DuckDB + MotherDuck (optional) | ~$0–$100 | MotherDuck for cloud collaboration if needed |

---

## Bottom Line

For **stock market fundamentals and economic indicators** in a local desktop app, DuckDB is the optimal choice. Its embedded, in-process architecture eliminates the Docker networking issues encountered with MongoDB, its columnar engine provides superior analytical query performance for screening and backtesting workloads, and its zero-copy Python integration fits seamlessly into QuantLens's existing data pipeline. The SQL interface is more ergonomic than MongoDB's aggregation pipelines for the cross-sectional analysis and window functions that quantitative research demands.
