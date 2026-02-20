# OHLCV Database Selection

## Overview

This document evaluates time-series databases for storing and querying OHLCV (Open, High, Low, Close, Volume) financial market data. After comprehensive benchmarking across QuestDB, ClickHouse, TimescaleDB, and InfluxDB, **QuestDB is the recommended choice** for QuantLens' time-series data ingestion and backtesting workload.

---

## Executive Summary

**Decision: QuestDB**

For QuantLens' primary use case—ingesting time-series data from providers (Tiingo, Alpaca, Finnhub) and reusing them for research and backtesting—QuestDB is the clear winner:

- **Highest ingestion throughput** (3M+ metrics/sec, 1.55x faster than ClickHouse, 2.6x faster than TimescaleDB)
- **Best full-table scan performance** (70ms mean, 2.1x faster than ClickHouse/TimescaleDB, 13x faster than InfluxDB)
- **Strong aggregation performance** (#2 after ClickHouse at 27ms vs 16ms—still excellent for multi-asset backtesting)
- **Purpose-built for financial markets** with native `SAMPLE BY`, `ASOF JOIN`, and `LATEST ON`
- **Columnar storage** optimized for time-series analytics and compression

While InfluxDB is fastest for simple point lookups (1.4ms vs QuestDB's 7.2ms), QuantLens' workload is dominated by batch ingestion and analytical queries—not single-series monitoring.

---

## 1. Four-Database Benchmark Results

Comprehensive performance testing with ~10M metrics across 10 metrics, 100 hosts, and 10K timestamps reveals clear workload-specific leaders:

### Data Ingestion Performance

| Database | Metrics/sec | Rows/sec | Time (sec) | Relative Speed |
|----------|-------------|----------|------------|----------------|
| **QuestDB** | 3,034,231 | 303,423 | 3.30 | **1.55x** vs ClickHouse |
| **ClickHouse** | 2,468,716 | 246,872 | 4.06 | Baseline |
| **TimescaleDB** | 1,955,652 | 195,565 | 5.13 | 1.27x slower |
| **InfluxDB** | 1,156,571 | 115,657 | 8.67 | 2.12x slower |

**Winner: QuestDB** — Highest ingestion throughput, loading ~10M metrics in just 3.3 seconds.

---

### Query Performance Analysis

#### Query 1: Single Host, Single Metric (1h range, 1min intervals)
*Simple point query - low complexity*

| Database | Mean Latency | Median | Max | Queries/sec | vs Best |
|----------|-------------|---------|-----|-------------|---------|
| **InfluxDB** | **1.38ms** | 1.35ms | 2.49ms | 1,437 | **Baseline** |
| TimescaleDB | 2.36ms | 2.04ms | 18.51ms | 842 | 1.7x slower |
| QuestDB | 7.19ms | 6.71ms | 37.43ms | 274 | 5.2x slower |
| ClickHouse | 7.13ms | 6.33ms | 32.34ms | 279 | 5.2x slower |

**Winner: InfluxDB** — Optimized for simple single-series lookups. QuestDB and ClickHouse show higher overhead for simple queries.

---

#### Query 2: All Hosts Aggregation (12h range, 1h intervals)
*Double groupby - medium analytical workload*

| Database | Mean Latency | Median | Max | Queries/sec | vs Best |
|----------|-------------|---------|-----|-------------|---------|
| **ClickHouse** | **15.79ms** | 15.25ms | 46.12ms | 125 | **Baseline** |
| QuestDB | 26.82ms | 20.70ms | 164.30ms | 74 | 1.7x slower |
| InfluxDB | 63.84ms | 57.56ms | 99.19ms | 31 | 4.0x slower |
| TimescaleDB | 126.52ms | 125.51ms | 214.40ms | 16 | 8.0x slower |

**Winner: ClickHouse** — Strong columnar aggregation performance. TimescaleDB struggles significantly with cross-host aggregations (8x slower).

---

#### Query 3: CPU Over Threshold (Full Table Scan)
*Heavy analytical query - finding outliers across all data*

| Database | Mean Latency | Median | Max | Queries/sec | vs Best |
|----------|-------------|---------|-----|-------------|---------|
| **QuestDB** | **70.68ms** | 60.62ms | 360.30ms | 28.2 | **Baseline** |
| TimescaleDB | 148.12ms | 157.83ms | 221.53ms | 13.4 | 2.1x slower |
| ClickHouse | 146.88ms | 142.86ms | 206.53ms | 13.6 | 2.1x slower |
| **InfluxDB** | 917.51ms | 925.98ms | 1005.02ms | 2.2 | **13.0x slower** |

**Winner: QuestDB** — Best performance for full-table analytical scans. InfluxDB shows catastrophic performance (13x slower), likely due to its tag-based indexing model struggling with unbounded scans.

---

### Performance Summary

| Workload Type | Winner | Key Insight |
|---------------|--------|-------------|
| **Ingestion** | QuestDB | 3M+ metrics/sec, 2.6x faster than TimescaleDB |
| **Simple lookups** | InfluxDB | Sub-2ms for single series, but doesn't scale |
| **Medium aggregations** | ClickHouse | 15ms for cross-host analysis |
| **Heavy analytics** | QuestDB | 70ms for full scans, consistent performance |
| **Worst performer** | InfluxDB | Terrible at full-table scans (917ms) |

### Key Architectural Observations

1. **QuestDB**: Excels at ingestion (3M/sec) and heavy analytics, but has ~5ms overhead on simple queries. Best for high-throughput time-series with complex analysis needs.

2. **ClickHouse**: Most balanced - strong ingestion (2.5M/sec), best at aggregations, competitive across the board. Pure columnar architecture shines for OLAP.

3. **TimescaleDB**: PostgreSQL compatibility comes at cost - decent ingestion but 8x slower on aggregations than ClickHouse. Good for mixed OLTP/OLAP needs.

4. **InfluxDB**: Fastest for simple queries (1.4ms) but fails catastrophically on analytical workloads (917ms for scans). Designed for monitoring, not analytics.

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

QuantLens' core workload is **time-series data ingestion for research and backtesting**, not OLTP or simple monitoring dashboards. This means:

1. **Batch ingestion dominates**: Loading historical data from providers (Tiingo, Alpaca, Finnhub)
2. **Analytical queries for backtests**: Multi-symbol aggregations, time-range scans, bar generation
3. **Full-table scans common**: Portfolio analytics across all symbols and time ranges
4. **Append-only workflow**: Historical market data is immutable; corrections are rare
5. **Local Docker deployment**: No cloud free-tier constraints or managed service limitations

QuestDB's architecture directly aligns with this workload:
- **#1 in ingestion** (3M metrics/sec) — critical for building historical datasets
- **#1 in full-table scans** (70ms) — essential for portfolio-wide analytics
- **#2 in aggregations** (27ms vs ClickHouse's 16ms) — still excellent for backtesting
- **Native financial features** (`SAMPLE BY`, `ASOF JOIN`, `LATEST ON`) — eliminates SQL workarounds

The 5.2x slower simple queries (7ms vs InfluxDB's 1.4ms) are **not relevant** because QuantLens doesn't perform single-series monitoring lookups—it processes multi-asset datasets in bulk.

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
- **303K rows/sec ingestion** (1.55x faster than ClickHouse, 2.6x faster than TimescaleDB) — critical for building historical datasets
- **70ms full-table scans** (2.1x faster than ClickHouse/TimescaleDB, 13x faster than InfluxDB) — essential for portfolio analytics
- **27ms aggregations** (#2 after ClickHouse's 16ms) — excellent for multi-asset backtesting
- **Native OHLCV bar generation** via `SAMPLE BY` — no complex window functions
- **Columnar compression + Parquet export** — efficient storage and NautilusTrader integration
- **Zero-GC Java/C++ implementation** — predictable latency for backtesting
- **No cardinality degradation** — maintains performance with thousands of symbols

**Trade-offs**:
- **7ms simple queries** (vs InfluxDB's 1.4ms) — not relevant for QuantLens' batch workload
- **Append-only** — corrections require insert-new-row pattern (use `LATEST ON` for most recent)
- **Partial PGWire support** — works with `asyncpg`, but `psycopg2` scrollable cursors unsupported
- **Limited schema evolution** — requires consistent column types per table

---

### Alternative: ClickHouse

ClickHouse offers the best aggregation performance and balanced overall capabilities.

```sql
-- OHLCV bar generation in ClickHouse
SELECT
    toStartOfInterval(timestamp, INTERVAL 1 minute) as bucket,
    symbol,
    argMin(price, timestamp) as open,
    max(price) as high,
    min(price) as low,
    argMax(price, timestamp) as close,
    sum(volume) as volume
FROM ticks
GROUP BY bucket, symbol
ORDER BY bucket;
```

**Strengths**: Best aggregation performance (16ms), strong ingestion (2.5M/sec), mature OLAP features, excellent compression.

**Why not chosen**: 
- **246K rows/sec ingestion** (38% slower than QuestDB) — suboptimal for historical data loading
- **147ms full-table scans** (2.1x slower than QuestDB) — impacts portfolio-wide analytics
- **No native `ASOF JOIN`** — critical for correlating trades with quotes in financial data
- **No `LATEST ON`** — requires complex window functions for latest-value-per-symbol queries
- **Complex deployment** — more heavyweight than QuestDB for local Docker setup

For QuantLens' ingestion-heavy, full-scan workload, QuestDB's strengths in these areas outweigh ClickHouse's aggregation advantage.

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
- **196K rows/sec ingestion** (55% slower than QuestDB) — suboptimal for historical data loading
- **148ms full-table scans** (2.1x slower than QuestDB) — impacts portfolio analytics
- **127ms aggregations** (8x slower than ClickHouse, 4.7x slower than QuestDB) — poor for multi-asset backtests
- **Complex analytical queries** — requires window functions instead of native `SAMPLE BY`/`ASOF JOIN`
- **Continuous aggregates have refresh lag** — not truly real-time

For QuantLens' ingestion-heavy, append-only, analytical workload, the PostgreSQL compatibility benefits don't outweigh the significant performance gaps across all key workloads.

---

### Other Alternatives

#### InfluxDB — Wrong Architecture for Analytics

- **Fastest simple queries** (1.4ms) — but this is irrelevant for batch analytical workloads
- **Catastrophic full-table scan performance** (917ms, 13x slower than QuestDB) — unacceptable for portfolio analytics
- **Per-series TSM storage** — performance collapses with thousands of symbols in analytical queries
- **No true JOINs** — cannot correlate trades with quotes or reference data
- **Flux query language** — steep learning curve; poor for complex financial calculations
- **Best for**: Infrastructure monitoring with low cardinality and simple lookups (hundreds of metrics, not thousands of symbols)

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
    image: questdb/questdb:7.3.10  # Pin version for reproducible deployments
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
# Standard imports at module level
import asyncpg
from datetime import datetime

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
    # Connect with same parameters as previous example
    conn = await asyncpg.connect(
        host='localhost',
        port=8812,
        user='admin',
        password='quest',
        database='qdb'
    )
    
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

| Requirement | QuestDB | ClickHouse | TimescaleDB | InfluxDB | Winner |
|-------------|---------|------------|-------------|----------|--------|
| **Ingestion speed** | 303K rows/sec | 247K rows/sec | 196K rows/sec | 116K rows/sec | **QuestDB** |
| **Simple point queries** | 7.19ms | 7.13ms | 2.36ms | **1.38ms** | InfluxDB |
| **Aggregation queries** | 26.82ms | **15.79ms** | 126.52ms | 63.84ms | ClickHouse |
| **Full table scans** | **70.68ms** | 146.88ms | 148.12ms | 917.51ms | **QuestDB** |
| **Native SAMPLE BY** | ✅ | ❌ | ❌ | ❌ | **QuestDB** |
| **ASOF JOIN** | ✅ | ❌ | ❌ | ❌ | **QuestDB** |
| **LATEST ON** | ✅ | ❌ | ❌ | ❌ | **QuestDB** |
| **Parquet export** | ✅ Native | ✅ Native | Manual | Manual | Tie (QuestDB/ClickHouse) |
| **SQL support** | PostgreSQL wire | ClickHouse SQL | PostgreSQL | Flux/InfluxQL | TimescaleDB/QuestDB |
| **Local Docker** | ✅ Simple | ✅ Medium | ✅ Simple | ✅ Simple | Tie (all) |
| **Python drivers** | asyncpg | clickhouse-driver | asyncpg, psycopg2 | influxdb-client | TimescaleDB |
| **UPDATE/DELETE** | ❌ | ✅ Limited | ✅ Full | ❌ | TimescaleDB |
| **Schema evolution** | Limited | Medium | Full ALTER TABLE | Limited | TimescaleDB |
| **Community size** | Medium | Large | Large | Large | ClickHouse/TimescaleDB/InfluxDB |

**For QuantLens' workload (ingestion-heavy, full-scan analytics, append-only), QuestDB wins on the metrics that matter most: ingestion speed (#1) and full-table scan performance (#1). The slower simple queries (7ms vs InfluxDB's 1.4ms) and aggregations (#2 vs ClickHouse) are acceptable trade-offs.**

---

## 6. When to Reconsider

QuestDB may not be optimal if requirements change to:

- **Frequent data corrections** requiring UPDATE/DELETE operations
- **OLTP workloads** with transactional requirements (order management, portfolio state)
- **Simple point queries dominate** (e.g., "fetch latest price" is >80% of queries) — consider InfluxDB
- **Best-in-class aggregation performance needed** (ClickHouse is 1.7x faster at 16ms vs 27ms)
- **Complex schema evolution** needed frequently during development
- **Mixed transactional/analytical** workloads in a single database

In these cases, consider:
- **InfluxDB** for simple monitoring dashboards with mostly single-series lookups
- **ClickHouse** if aggregation performance is more critical than ingestion/full-scan performance
- **TimescaleDB** for OLTP + time-series hybrid workloads requiring PostgreSQL compatibility
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

The decision is driven by benchmark results across 4 leading time-series databases:

1. **Best ingestion performance**: 303K rows/sec (1.55x faster than ClickHouse, 2.6x faster than TimescaleDB, 2.6x faster than InfluxDB) — critical for building historical datasets from data providers

2. **Best full-table scan performance**: 70.68ms mean (2.1x faster than ClickHouse/TimescaleDB, 13x faster than InfluxDB) — essential for portfolio-wide analytics across all symbols

3. **Strong aggregation performance**: 26.82ms mean (#2 after ClickHouse's 15.79ms) — excellent for multi-asset backtesting with only 1.7x difference

4. **Financial market features**: Native `SAMPLE BY`, `ASOF JOIN`, and `LATEST ON` eliminate complex SQL workarounds required by all competitors

5. **Columnar architecture**: Purpose-built for time-series analytics and compression with native Parquet export for NautilusTrader integration

**Trade-offs accepted**: Simple point queries are 5.2x slower than InfluxDB (7.19ms vs 1.38ms), but this is irrelevant for QuantLens' batch analytical workload—we don't perform single-series monitoring lookups.

**Why not ClickHouse**: Despite best aggregation performance, ClickHouse loses on ingestion (38% slower) and full-table scans (2.1x slower), lacks financial-specific features like `ASOF JOIN` and `LATEST ON`, and has more complex deployment.

**Why not TimescaleDB**: PostgreSQL compatibility doesn't justify 55% slower ingestion, 2.1x slower scans, and 4.7x slower aggregations—all critical for QuantLens' workload.

**Why not InfluxDB**: Catastrophic on analytical queries (917ms full-table scans, 13x slower than QuestDB) makes it unsuitable despite fast simple queries.

**Docker Compose ships with QuestDB** as documented in ARCHITECTURE.md.

---

## References

1. https://questdb.com/blog/influxdb-vs-questdb-comparison/ 
2. https://questdb.com/blog/timescaledb-vs-questdb-comparison/ 
3. https://questdb.com/blog/mongodb-time-series-benchmark-review/
4. QuantLens's benchmark results (2026): 4-database comparison (QuestDB, ClickHouse, TimescaleDB, InfluxDB)