# Fundamentals & Economic Data Storage

## Overview

This document evaluates storage options for stock market fundamentals and economic indicators. Unlike OHLCV tick data (covered in [ohlcv_database.md](ohlcv_database.md)), fundamentals and economic data are semi-structured, low-frequency, and high-dimensionality — characteristics that favor flexible schema handling and analytical query performance over raw write throughput.

**Decision: DuckDB** — an embedded, in-process analytical database that eliminates Docker networking complexity and provides best-in-class DX for quantitative research on local desktop deployments.

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
- Competitive with ClickHouse on ClickBench benchmarks for analytical queries
- **In-memory or on-disk**: Load multi-GB datasets into memory for iteration speed, or query larger-than-RAM datasets efficiently

### Perfect Fit for Backtesting Workloads

- **Zero-copy integration** with Pandas/Polars — critical for strategy prototyping
- **Window functions** for financial calculations (`LEAD`, `LAG`, rolling averages, YoY comparisons)
- **Native Parquet support** — seamless integration with NautilusTrader's `ParquetDataCatalog` and the existing data pipeline

### When to Consider Alternatives

| Database | Use If | Why Not for QuantLens |
|----------|--------|----------------------|
| **QuestDB** | Need real-time ingestion + SQL | Already used for OHLCV; overkill for batch fundamentals |
| **TimescaleDB** | Need ACID + time-series + PostgreSQL ecosystem | Docker complexity; row-store heritage slows wide analytical scans |
| **ClickHouse** | Petabyte-scale, distributed | Heavy infrastructure; embedded DuckDB beats it for local GB-scale data |
| **MongoDB** | Need document model with managed cloud deployment | Docker connection issues for local use; aggregation syntax less ergonomic than SQL |

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

### Alternatives Considered

| Database | Verdict | Reason |
|----------|---------|--------|
| **MongoDB (Docker)** | Previously recommended; replaced | Docker connection errors in local desktop deployment; aggregation pipeline syntax less ergonomic than SQL for analytical queries; unnecessary infrastructure overhead for a single-user embedded use case |
| **MongoDB Atlas (M0)** | Not suitable for local-first | Requires cloud connectivity; contradicts QuantLens's local-first, no-cloud-accounts-required philosophy |
| **PostgreSQL** | Already used for strategies/results | Could store fundamentals, but lacks DuckDB's columnar performance for analytical scans; would overload the OLTP database with OLAP queries |
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
