# OHLCV Database Selection

## Overview

This document evaluates time-series databases for storing and querying OHLCV (Open, High, Low, Close, Volume) financial market data. After comprehensive benchmarking of QuestDB vs TimescaleDB and evaluating architectural trade-offs, **QuestDB is the recommended choice** for QuantLens' time-series data ingestion and backtesting workload.

---

## Executive Summary

**Decision: QuestDB**

For QuantLens' primary use case—ingesting time-series data from providers (Tiingo, Alpaca, Finnhub) and reusing them for research and backtesting—QuestDB is the clear winner:

- **1.7x faster data ingestion** (critical for building historical datasets)
- **4.7x faster aggregations** across all symbols (essential for multi-asset backtesting)
- **2.1x faster full table scans** (typical for portfolio analytics)
- **Purpose-built for financial markets** with native `SAMPLE BY`, `ASOF JOIN`, and `LATEST ON`
- **Columnar storage** optimized for time-series analytics and compression

While TimescaleDB excels at simple point lookups (2.6x faster), our workload is dominated by batch ingestion and analytical queries—not OLTP operations.

---

## 1. QuestDB vs TimescaleDB Benchmark Results

Recent performance testing with 1M rows across 10 metrics and 100 hosts reveals clear workload-specific winners:

### Data Loading Performance

| Metric | QuestDB | TimescaleDB | Advantage |
|--------|---------|-------------|-----------|
| **Load Time** | 3.012 sec | 5.154 sec | QuestDB 1.7x faster |
| **Metrics/sec** | 3,327,761 | 1,944,415 | QuestDB 1.7x faster |
| **Rows/sec** | 332,776 | 194,442 | QuestDB 1.7x faster |
| **Total Metrics** | 10,022,400 | 10,022,400 | Same |
| **Total Rows** | 1,002,240 | 1,002,240 | Same |

**Winner: QuestDB** — ~71% faster ingestion with 2 workers

### Query Performance Analysis

#### Test 1: Single Metric, Single Host, 1 Hour (100 points)
Simple point lookup query

| Metric | QuestDB | TimescaleDB | Advantage |
|--------|---------|-------------|-----------|
| **Mean Latency** | 6.36 ms | 2.44 ms | TimescaleDB 2.6x faster |
| **Median** | 5.53 ms | 2.05 ms | TimescaleDB 2.7x faster |
| **Min** | 1.00 ms | 1.67 ms | QuestDB slightly faster |
| **Max** | 38.28 ms | 18.45 ms | TimescaleDB 2.1x faster |
| **Throughput** | 308.76 q/s | 812.80 q/s | TimescaleDB 2.6x faster |

**Winner: TimescaleDB** — Significantly faster for simple point queries

#### Test 2: Mean of 1 Metric, All Hosts, 12 Hours by 1h
Aggregation across all hosts with grouping

| Metric | QuestDB | TimescaleDB | Advantage |
|--------|---------|-------------|-----------|
| **Mean Latency** | 28.61 ms | 134.37 ms | **QuestDB 4.7x faster** |
| **Median** | 23.29 ms | 132.34 ms | **QuestDB 5.7x faster** |
| **Min** | 13.60 ms | 116.26 ms | **QuestDB 8.5x faster** |
| **Max** | 197.11 ms | 200.26 ms | Similar |
| **Throughput** | 68.72 q/s | 14.87 q/s | **QuestDB 4.6x faster** |

**Winner: QuestDB** — Dramatically faster for analytical aggregations

#### Test 3: CPU Over Threshold, All Hosts
Full table scan with filtering

| Metric | QuestDB | TimescaleDB | Advantage |
|--------|---------|-------------|-----------|
| **Mean Latency** | 71.78 ms | 152.31 ms | **QuestDB 2.1x faster** |
| **Median** | 60.13 ms | 156.08 ms | **QuestDB 2.6x faster** |
| **Min** | 41.18 ms | 98.70 ms | **QuestDB 2.4x faster** |
| **Max** | 318.33 ms | 213.18 ms | TimescaleDB 1.5x faster |
| **Throughput** | 27.64 q/s | 13.04 q/s | **QuestDB 2.1x faster** |

**Winner: QuestDB** — ~2x faster for full table scans

### Performance Summary

| Workload Type | Winner | Margin |
|--------------|--------|--------|
| **Data Ingestion** | QuestDB | 1.7x faster |
| **Simple Point Queries** | TimescaleDB | 2.6x faster |
| **Aggregations (All Hosts)** | QuestDB | 4.7x faster |
| **Full Table Scans** | QuestDB | 2.1x faster |

### Key Insights

1. **Trade-off Pattern**: TimescaleDB excels at simple point lookups (leveraging PostgreSQL's B-tree indexes), while QuestDB dominates analytical workloads (leveraging columnar storage and SIMD)

2. **Cardinality Scaling**: QuestDB shows better performance as query complexity increases (moving from single-host to all-hosts queries)

3. **Consistency**: QuestDB has higher variance in simple queries (stddev 5.42ms vs 2.30ms) but lower variance in complex queries

4. **Architecture Difference**: 
   - TimescaleDB's PostgreSQL roots give it fast index lookups
   - QuestDB's columnar design gives it superior scan and aggregation performance

---

## 2. OHLCV-Specific Requirements

Financial OHLCV data has characteristics that favor QuestDB's architecture:

- **High cardinality**: Thousands of symbols across multiple exchanges, each with bid/ask/trade streams.
- **Append-heavy**: Market data is immutable facts; corrections arrive as new events.
- **Tick-to-bar pipeline**: Raw ticks need downsampling into OHLCV bars (1m, 5m, 1h, 1d).
- **Temporal joins**: Correlating trades with quotes at nearest timestamps (ASOF JOIN).
- **Last-value queries**: "What's the latest price for symbol X?" must be fast.
- **SQL familiarity**: Quant teams expect SQL for backtesting and analysis with pandas/SQLAlchemy.

---

## 3. Why QuestDB for QuantLens

### Primary Use Case Alignment

QuantLens' core workload is **time-series data ingestion for research and backtesting**, not OLTP or real-time analytics dashboards. This means:

1. **Batch ingestion dominates**: Loading historical data from providers (Tiingo, Alpaca, Finnhub)
2. **Analytical queries for backtests**: Multi-symbol aggregations, time-range scans, bar generation
3. **Append-only workflow**: Historical market data is immutable; corrections are rare
4. **Local Docker deployment**: No cloud free-tier constraints or managed service limitations

QuestDB's architecture is purpose-built for exactly this workload.

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

**Strengths for QuantLens**:
- **332K rows/sec ingestion** (vs TimescaleDB's 194K) — critical for building historical datasets
- **Native OHLCV bar generation** via `SAMPLE BY` — no complex window functions
- **Columnar compression + Parquet export** — efficient storage and NautilusTrader integration
- **Zero-GC Java/C++ implementation** — predictable latency for backtesting
- **No cardinality degradation** — maintains performance with thousands of symbols

**Trade-offs**:
- **Append-only** — corrections require insert-new-row pattern (use `LATEST ON` for most recent)
- **Partial PGWire support** — works with `asyncpg`, but `psycopg2` scrollable cursors unsupported
- **Limited schema evolution** — requires consistent column types per table

---

### Alternative: TimescaleDB

TimescaleDB offers full PostgreSQL compatibility with time-based partitioning (hypertables).

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

**Strengths**: Full UPDATE/DELETE for trade corrections, any PostgreSQL driver/ORM works, hybrid row-columnar storage, mature ecosystem, handles mixed OLTP/OLAP workloads.

**Why not chosen**: 
- **194K rows/sec ingestion** (41% slower than QuestDB) — suboptimal for historical data loading
- **4.7x slower aggregations** — impacts multi-asset backtest queries
- **Complex analytical queries** — requires window functions instead of native `SAMPLE BY`/`ASOF JOIN`
- **Continuous aggregates have refresh lag** — not truly real-time

For QuantLens' ingestion-heavy, append-only, analytical workload, the PostgreSQL compatibility benefits don't outweigh the performance gap.

---

### Other Alternatives

- **Per-series TSM storage**: Performance collapses with thousands of symbols.
- **No true JOINs**: Cannot correlate trades with quotes or reference data.
- **Flux query language**: Steep learning curve; poor for complex financial calculations.
- **Best for**: Infrastructure monitoring with low cardinality (hundreds of metrics, not thousands of symbols).

### MongoDB — General-Purpose Misfit

- **Document model overhead**: Uniform OHLCV schema doesn't benefit from flexible documents.
- **No time-series optimizations**: No native downsampling, partition pruning, or SIMD vectorization.
- **24x slower ingestion**: Cannot keep up with market data feeds.
- **Complex aggregation pipelines**: 20+ lines for what QuestDB does in 5 lines of SQL.

#### InfluxDB — Wrong Architecture for Finance

- **Per-series TSM storage**: Performance collapses with thousands of symbols.
- **No true JOINs**: Cannot correlate trades with quotes or reference data.
- **Flux query language**: Steep learning curve; poor for complex financial calculations.
- **Best for**: Infrastructure monitoring with low cardinality (hundreds of metrics, not thousands of symbols).

#### MongoDB — General-Purpose Misfit

- **Document model overhead**: Uniform OHLCV schema doesn't benefit from flexible documents.
- **No time-series optimizations**: No native downsampling, partition pruning, or SIMD vectorization.
- **24x slower ingestion**: Cannot keep up with market data feeds.
- **Complex aggregation pipelines**: 20+ lines for what QuestDB does in 5 lines of SQL.

---

## 4. Implementation Strategy

### Docker Compose Configuration

QuestDB runs locally via Docker Compose with the following services:

```yaml
services:
  questdb:
    image: questdb/questdb:latest
    ports:
      - "9000:9000"  # REST API and Web Console
      - "8812:8812"  # PostgreSQL wire protocol
      - "9009:9009"  # InfluxDB line protocol (recommended for ingestion)
    volumes:
      - questdb-data:/var/lib/questdb
    environment:
      - QDB_PG_ENABLED=true
      - QDB_HTTP_ENABLED=true
      - QDB_LINE_TCP_ENABLED=true
```

### Data Ingestion Pattern

Use InfluxDB Line Protocol (ILP) over TCP for maximum ingestion performance:

```python
import socket

def ingest_ohlcv(symbol: str, bars: list[dict]):
    """Ingest OHLCV bars using ILP over TCP (9009)"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(('localhost', 9009))
    
    for bar in bars:
        line = (
            f"ohlcv,symbol={symbol} "
            f"open={bar['open']},high={bar['high']},low={bar['low']},"
            f"close={bar['close']},volume={bar['volume']} "
            f"{bar['timestamp']}\n"
        )
        sock.sendall(line.encode())
    
    sock.close()
```

### Query Pattern

Use PostgreSQL wire protocol (asyncpg) for analytical queries:

```python
import asyncpg

async def fetch_bars(symbol: str, start: datetime, end: datetime):
    """Fetch OHLCV bars using PGWire protocol (8812)"""
    conn = await asyncpg.connect(
        host='localhost',
        port=8812,
        user='admin',
        password='quest',
        database='qdb'
    )
    
    rows = await conn.fetch("""
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv
        WHERE symbol = $1
          AND timestamp BETWEEN $2 AND $3
        ORDER BY timestamp
    """, symbol, start, end)
    
    await conn.close()
    return rows
```

### QuestDB → Parquet Export for NautilusTrader

```python
async def export_to_parquet(symbol: str, output_path: str):
    """Export QuestDB data to Parquet for NautilusTrader ParquetDataCatalog"""
    conn = await asyncpg.connect(host='localhost', port=8812, ...)
    
    # Fetch data
    rows = await conn.fetch("""
        SELECT * FROM ohlcv
        WHERE symbol = $1
        ORDER BY timestamp
    """, symbol)
    
    # Convert to Parquet
    df = pd.DataFrame(rows)
    df.to_parquet(output_path, engine='pyarrow', compression='snappy')
```

### Handling Data Corrections

QuestDB is append-only. For corrections, insert a new row with updated data and use `LATEST ON`:

```sql
-- Insert correction
INSERT INTO ohlcv VALUES (
    '2024-01-15T09:30:00.000000Z',  -- original timestamp
    'AAPL',
    185.00,  -- corrected open
    186.50,  -- corrected high
    184.00,  -- corrected low
    186.00,  -- corrected close
    1000000  -- corrected volume
);

-- Query always returns latest version per timestamp+symbol
SELECT * FROM ohlcv
LATEST ON timestamp PARTITION BY symbol
WHERE symbol = 'AAPL';
```

---

## 5. Decision Matrix

| Requirement | QuestDB | TimescaleDB | Winner |
|-------------|---------|-------------|--------|
| **Data ingestion speed** | 332K rows/sec | 194K rows/sec | **QuestDB (1.7x)** |
| **Aggregation queries** | 28.61ms mean | 134.37ms mean | **QuestDB (4.7x)** |
| **Full table scans** | 71.78ms mean | 152.31ms mean | **QuestDB (2.1x)** |
| **Simple point queries** | 6.36ms mean | 2.44ms mean | TimescaleDB (2.6x) |
| **Native SAMPLE BY** | ✅ | ❌ (time_bucket) | **QuestDB** |
| **ASOF JOIN** | ✅ | ❌ (LATERAL) | **QuestDB** |
| **LATEST ON** | ✅ | ❌ (DISTINCT ON) | **QuestDB** |
| **Parquet export** | ✅ Native | Manual | **QuestDB** |
| **Local Docker** | ✅ Simple | ✅ Simple | Tie |
| **Python drivers** | asyncpg | asyncpg, psycopg2 | TimescaleDB |
| **UPDATE/DELETE** | ❌ (append-only) | ✅ | TimescaleDB |
| **Schema evolution** | Limited | Full ALTER TABLE | TimescaleDB |
| **Community size** | Smaller | Larger | TimescaleDB |

**For QuantLens' workload (ingestion-heavy, append-only, analytical queries), QuestDB's strengths heavily outweigh its limitations.**

---

## 6. When to Reconsider

QuestDB may not be optimal if requirements change to:

- **Frequent data corrections** requiring UPDATE/DELETE operations
- **OLTP workloads** with transactional requirements (order management, portfolio state)
- **Simple point queries dominate** (e.g., "fetch latest price" is >80% of queries)
- **Complex schema evolution** needed frequently during development
- **Mixed transactional/analytical** workloads in a single database

In these cases, consider:
- **TimescaleDB** for OLTP + time-series hybrid workloads
- **PostgreSQL + QuestDB** dual-database architecture (PostgreSQL for OLTP, QuestDB for time-series)

---

## 7. Migration Path

If switching from TimescaleDB to QuestDB later:

1. **Data export**: Use TimescaleDB's `COPY TO` command or `pg_dump`
2. **Transform**: Convert to InfluxDB Line Protocol format
3. **Bulk load**: Use QuestDB's ILP over TCP for fast ingestion
4. **Schema mapping**: Map TimescaleDB hypertables to QuestDB tables
5. **Query rewrite**: Replace `time_bucket()` with `SAMPLE BY`, window functions with `LATEST ON`

Estimated migration time for 100M rows: ~30 minutes (at 300K rows/sec ingestion rate).

---

## 8. Conclusion

**QuestDB is the recommended time-series database for QuantLens.**

The decision is driven by:

1. **Performance alignment**: QuestDB's 1.7x faster ingestion, 4.7x faster aggregations, and 2.1x faster scans directly match QuantLens' ingestion-heavy, analytical workload
2. **Financial market features**: Native `SAMPLE BY`, `ASOF JOIN`, and `LATEST ON` eliminate complex SQL workarounds
3. **Columnar architecture**: Purpose-built for time-series analytics and compression
4. **Local deployment**: Running in Docker eliminates cloud free-tier constraints that previously favored TimescaleDB
5. **NautilusTrader integration**: Native Parquet export streamlines the QuestDB → ParquetDataCatalog pipeline

While TimescaleDB offers better PostgreSQL compatibility and OLTP capabilities, these benefits are not critical for QuantLens' core use case of ingesting time-series data for research and backtesting.

**Docker Compose ships with QuestDB** as documented in system_design.md.

---

## References

1. https://questdb.com/blog/influxdb-vs-questdb-comparison/ 
2. https://questdb.com/blog/timescaledb-vs-questdb-comparison/ 
3. https://questdb.com/blog/mongodb-time-series-benchmark-review/
4. Internal benchmark results (2026): QuestDB vs TimescaleDB performance testing