# OHLCV Database Selection

## Overview

This document evaluates time-series databases for storing and querying OHLCV (Open, High, Low, Close, Volume) financial market data. We assess QuestDB, TimescaleDB, InfluxDB, and MongoDB—examining performance claims, architectural trade-offs, and suitability for our phased rollout starting with Finnhub + Alpaca free-tier data.

---

## 1. QuestDB Performance Assessment

QuestDB markets itself as the "fastest" time-series database. After analyzing their comparison blog posts and independent benchmarks, the claim is **substantially accurate for analytical workloads**, but comes with important context.

### Where QuestDB Genuinely Excels

**Ingestion Performance (Validated)**
- **11.36M rows/sec** at peak (100K hosts) vs InfluxDB's 203K–402K rows/sec
- **6–13x faster** than TimescaleDB across all cardinality levels
- **24x faster** than MongoDB for time-series ingestion
- Maintains performance even at **1M+ cardinality** where competitors degrade significantly

**Analytical Query Performance (Validated)**
- **21–130x faster** than InfluxDB on double-groupby aggregations
- **16–20x faster** than TimescaleDB on complex analytical queries
- **375–418x faster** than InfluxDB 3 Core on extended range queries

**Root cause**: Columnar storage + SIMD vectorization + single-table architecture eliminates per-series overhead that cripples InfluxDB at high cardinality.

### Known Catches & Limitations

| # | Limitation | Detail |
|---|-----------|--------|
| 1 | **Simple point queries lag** | InfluxDB v1 is ~2.5x faster on single-series lookups (`single-groupby-1-1-1`). If workload is primarily "fetch latest value for one sensor," InfluxDB may be better. |
| 2 | **Append-only / no OLTP** | No true UPDATE/DELETE. Not designed for transactional workloads or log monitoring with frequent modifications. |
| 3 | **Ecosystem immaturity** | Younger project (2019 vs 2013 InfluxDB, 2016 TimescaleDB). Fewer third-party integrations. |
| 4 | **Production reliability concerns** | Independent 2023 user reports: *"Complicated queries were prone to returning no results, wrong results, error conditions, or slowly… defaults were quirky."* Thorough testing advised. |
| 5 | **Benchmark methodology biases** | TSBS doesn't use bind variables; benchmarks run on high-end AWS instances (r8a.8xlarge, c6a.12xlarge); competitors tested with defaults. |
| 6 | **Enterprise features gated** | Replication, RBAC, TLS, compression are proprietary/closed-source. |
| 7 | **Schema rigidity** | Requires consistent column types per table. Variable data types across symbols require separate tables or casting. |
| 8 | **PostgreSQL compatibility gaps** | No scrollable cursors (`DECLARE CURSOR`); breaks some drivers like `psycopg2`. |

---

## 2. OHLCV-Specific Requirements

Financial OHLCV data has characteristics that narrow the database choice:

- **High cardinality**: Thousands of symbols across multiple exchanges, each with bid/ask/trade streams.
- **Append-heavy**: Market data is immutable facts; corrections arrive as new events.
- **Tick-to-bar pipeline**: Raw ticks need downsampling into OHLCV bars (1m, 5m, 1h, 1d).
- **Temporal joins**: Correlating trades with quotes at nearest timestamps (ASOF JOIN).
- **Last-value queries**: "What's the latest price for symbol X?" must be fast.
- **SQL familiarity**: Quant teams expect SQL for backtesting and analysis with pandas/SQLAlchemy.

---

## 3. Database Comparison for OHLCV

### QuestDB — Purpose-Built for Financial Markets

Originally designed for high-frequency trading infrastructure. Core features directly address financial data needs:

- **`SAMPLE BY`**: Native OHLCV bar generation from tick data
- **`ASOF JOIN`**: Join trades with quotes at nearest timestamps
- **`LATEST ON`**: Efficient last-value-per-symbol without expensive `GROUP BY`
- **Materialized views**: Auto-updating OHLCV bars as new ticks arrive

```sql
-- Generate 1-minute OHLCV bars from tick data
SELECT
    timestamp,
    symbol,
    first(price) as open,
    max(price) as high,
    min(price) as low,
    last(price) as close,
    sum(size) as volume
FROM trades
SAMPLE BY 1m;
```

**Strengths for OHLCV**: 11.36M rows/sec ingestion, no cardinality degradation, columnar compression + native Parquet export, zero-GC Java/C++ implementation for predictable latency.

**Weaknesses for OHLCV**: Append-only (corrections require insert-new-row pattern), partial PGWire support breaks some Python drivers, limited schema evolution during writes.

### TimescaleDB — PostgreSQL with Time-Series Extensions

Full PostgreSQL compatibility with automatic time-based partitioning (hypertables).

```sql
-- OHLCV bar generation in TimescaleDB
SELECT time_bucket('1 minute', timestamp) as bucket,
       symbol,
       first(price, timestamp) as open,
       max(price) as high,
       min(price) as low,
       last(price, timestamp) as close,
       sum(volume) as volume
FROM ticks
GROUP BY bucket, symbol;
```

**Strengths for OHLCV**: Full UPDATE/DELETE for trade corrections, any PostgreSQL driver/ORM works (`psycopg2`, `asyncpg`, `sqlalchemy`), hybrid row-columnar storage, mature ecosystem, handles mixed OLTP/OLAP (order management + analytics in one DB).

**Weaknesses for OHLCV**: No native `ASOF JOIN`, `LATEST ON`, or `SAMPLE BY` (requires complex window functions / LATERAL JOINs), ~1M rows/sec ingestion (degrades to 620K at high cardinality), continuous aggregates have refresh lag (not truly real-time), chunk interval tuning complexity.

### InfluxDB — Wrong Architecture for Finance

- **Per-series TSM storage**: Performance collapses with thousands of symbols.
- **No true JOINs**: Cannot correlate trades with quotes or reference data.
- **Flux query language**: Steep learning curve; poor for complex financial calculations.
- **Best for**: Infrastructure monitoring with low cardinality (hundreds of metrics, not thousands of symbols).

### MongoDB — General-Purpose Misfit

- **Document model overhead**: Uniform OHLCV schema doesn't benefit from flexible documents.
- **No time-series optimizations**: No native downsampling, partition pruning, or SIMD vectorization.
- **24x slower ingestion**: Cannot keep up with market data feeds.
- **Complex aggregation pipelines**: 20+ lines for what QuestDB does in 5 lines of SQL.

### Head-to-Head Ingestion Comparison

| Database | Raw Tick Ingestion | OHLCV Query Performance |
|----------|-------------------|------------------------|
| **QuestDB** | 11.36M rows/sec | Native `SAMPLE BY` optimization |
| TimescaleDB | ~1.2M rows/sec | Requires continuous aggregates |
| InfluxDB | ~400K rows/sec | Flux queries slower for financial math |
| MongoDB | ~300K rows/sec | Aggregation pipeline complex for time-series |

---

## 4. Recommendation

### Phase 1: TimescaleDB

For our starting constraints—**Finnhub + Alpaca free-tier real-time data**—TimescaleDB is the better choice. At small scale, developer productivity beats raw performance.

| Factor | Phase 1 Reality | Winner |
|--------|----------------|--------|
| Data volume | Free tiers ≈ 100–1,000 symbols, 1–5 min delayed or limited real-time | Either works |
| Ingestion rate | ~10K–50K ticks/sec max | TimescaleDB's 1M rows/sec is plenty |
| Query complexity | Mostly "latest price" and simple daily bars | Both handle easily |
| Development speed | Need to ship quickly, learn as you go | **TimescaleDB** (better docs, bigger community) |
| Tooling | Python pandas, SQLAlchemy, Jupyter | **TimescaleDB** (full PostgreSQL compatibility) |
| Future flexibility | May need order tracking, portfolio state, user accounts | **TimescaleDB** (ACID transactions, JOINs) |

**Key Phase 1 advantages of TimescaleDB:**

1. **PostgreSQL ecosystem**: Use `psycopg2`, `asyncpg`, or `sqlalchemy` without compatibility quirks.
2. **Correction handling**: Free-tier data has errors/delays. `UPDATE` bad bars directly instead of inserting correction rows.
3. **Schema evolution**: Early projects change schema frequently. Standard `ALTER TABLE` support.
4. **Learning resources**: More Stack Overflow answers, tutorials, and examples at small scale.
5. **Path to production**: PostgreSQL skills and tooling transfer directly if self-hosting or moving to TimescaleDB Cloud.

**Phase 1 schema:**

```sql
CREATE TABLE ohlcv (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    PRIMARY KEY (time, symbol)
);

SELECT create_hypertable('ohlcv', 'time', chunk_time_interval => INTERVAL '1 day');
```

### When to Switch to QuestDB (Phase 2+)

- Ingesting **>500K ticks/sec** or tracking **>10,000 symbols**
- Requiring **sub-10ms query latency** for real-time strategies
- Generating **OHLCV from raw ticks in real-time** rather than storing pre-aggregated bars
- Needing **ASOF JOIN** for correlating trades with quotes or multiple data streams

### Target Architecture (Phase 3)

```
Market Data Feed (ITCH, OUCH, FIX)
         │
    QuestDB (Raw Ticks)
         │
    ┌────┴────┐
    │         │
Materialized   Ad-hoc Analytics
Views (OHLCV)  (Backtesting, Research)
    │
Real-time Dashboards (Grafana)
```

- **QuestDB**: Raw market data, real-time signals, high-frequency analytics
- **TimescaleDB/PostgreSQL**: Order management, risk, reporting, reference data, user accounts

---

## When QuestDB IS the Right Choice

- High-frequency financial market data (tick data, order books)
- IoT at massive scale (millions of devices, high cardinality)
- Time-series analytics (aggregations, downsampling, ASOF JOINs)
- SQL-first teams wanting PostgreSQL compatibility
- Append-only workloads where ingestion speed is critical

## When to Look Elsewhere

- Simple monitoring dashboards with low cardinality → InfluxDB
- Mixed transactional/analytical workloads → TimescaleDB/PostgreSQL
- Document-style flexible schemas → MongoDB
- Sub-millisecond single-series lookups → InfluxDB v1
- Mature ecosystem requirements → InfluxDB (Telegraf integration)

---

## Bottom Line

QuestDB's "fastest" claim is **defensible for analytical time-series workloads at high cardinality**, but speed comes from architectural trade-offs (columnar, append-only) that sacrifice flexibility and OLTP capabilities. The 2025 benchmarks against InfluxDB 3 Core (Alpha) show QuestDB maintaining a **12–36x ingestion advantage** and **17–418x query advantage**, confirming the performance lead persists against modern Arrow-based architectures.

For **Phase 1 at free-tier scale**, TimescaleDB's PostgreSQL compatibility, mutable data, and larger community minimize friction. QuestDB becomes the clear upgrade path when data volumes and latency requirements demand it.

---

## Sources

1. https://questdb.com/blog/influxdb-vs-questdb-comparison/ 
2. https://questdb.com/blog/timescaledb-vs-questdb-comparison/ 
3. https://questdb.com/blog/mongodb-time-series-benchmark-review/