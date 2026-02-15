# NoSQL Database Selection

## Overview

This document evaluates document/NoSQL databases with free tiers for storing stock market fundamentals and economic indicators. Unlike OHLCV tick data (covered in [ohlcv_database.md](ohlcv_database.md)), fundamentals and economic data are semi-structured, low-frequency, and high-dimensionality — characteristics that favor a flexible document model over a fixed relational schema.

---

## 1. Why NoSQL for Fundamentals & Economic Data

Stock fundamentals (10-K/10-Q filings) and economic indicators (GDP, CPI, unemployment) differ from OHLCV data in ways that make traditional SQL less advantageous:

- **Variable schema**: SEC filings contain 100+ metrics that change across companies and reporting periods. Economic indicators vary by country, revision methodology, and frequency.
- **Nested structures**: Balance sheets, income statements, and cash flow statements are naturally hierarchical.
- **Schema evolution**: GDP calculation methodologies change over time; new reporting standards (e.g., IFRS vs GAAP) add fields without warning.
- **Low write frequency**: Quarterly/annual fundamentals and monthly/quarterly economic releases — not high-frequency streams.
- **Complex read patterns**: Screening queries (P/E < 15 AND revenue growth > 10%), cross-sectional analysis, and multi-dimensional aggregations.

---

## 2. Free Tier Comparison

| Database | Storage | Throughput/Operations | Key Limitation | Overage Risk |
|----------|---------|----------------------|----------------|--------------|
| **MongoDB Atlas (M0)** | 512 MB–5 GB | Shared vCPU/RAM | 512MB–5GB hard limit | **None** (hard stops) |
| **DataStax Astra** | 80 GB | 20M ops/month ($25 credit) | Credit exhaustion | **Medium** (pay-as-you-go kicks in) |
| **Azure Cosmos DB** | 25 GB | 1,000 RU/s | RU/s throttling | **High** (silent throttling, then charges) |
| **Google Firestore** | 1 GB | 50K reads/20K writes/day | Daily reset | **Low** (hard daily caps) |
| **Couchbase Capella** | 8 GB | 1 node, limited | Single node | **Low** (requires upgrade) |

### Why Some Providers Offer Massive Free Storage

DataStax Astra (80 GB) and Azure Cosmos DB (25 GB) monetize **throughput, not capacity**. They can afford generous storage because:

1. **Operation-based pricing**: High storage usage increases the likelihood of exceeding RU/operation limits due to larger index scans.
2. **Cassandra's LSM Tree architecture** (Astra): Append-only writes make storage cheap, but compaction and high-volume operations burn through credits quickly.
3. **Lock-in economics**: Querying 25 GB of data with complex aggregations consumes RUs rapidly. At ~$0.008 per 100 RU/s/hour, exceeding Cosmos DB's 1,000 RU/s limit costs ~$6/month per 100 RU/s.

### Architectural Differences

| Aspect | MongoDB (B-Tree) | Cassandra (LSM Tree) |
|--------|------------------|---------------------|
| Storage overhead | High (indexes, padding) | Low (immutable SSTables) |
| Write pattern | In-place updates | Append-only |
| Compaction cost | Low | High (background I/O) |
| Query flexibility | High (secondary indexes) | Low (partition key required) |

---

## 3. Cassandra's Write-Optimized Architecture

Cassandra (used by DataStax Astra) prioritizes write speed through a deliberate denormalization strategy:

```
Write Path:
1. Write to Commit Log (sequential disk append)
2. Write to Memtable (in-memory structure)
3. Acknowledge write to client
4. Later: Flush Memtable to SSTable (immutable, sorted file)
5. Later: Compaction merges SSTables (background process)
```

Cassandra achieves 1M+ writes/second by **never reading before writing**. Instead of maintaining a single normalized source of truth, it uses **query-driven denormalization** — writing the same data to multiple tables optimized for different access patterns:

```cql
BEGIN BATCH
    -- Table 1: Time-series by symbol (for range queries)
    INSERT INTO stock_prices_by_symbol (symbol, timestamp, price, volume)
    VALUES ('AAPL', '2024-01-01T09:30:00Z', 185.50, 10000);

    -- Table 2: Time-series by date (for daily aggregations)
    INSERT INTO stock_prices_by_date (date, symbol, price, volume)
    VALUES ('2024-01-01', 'AAPL', 185.50, 10000);

    -- Table 3: Latest price lookup (materialized view pattern)
    INSERT INTO latest_prices (symbol, timestamp, price)
    VALUES ('AAPL', '2024-01-01T09:30:00Z', 185.50);
END BATCH;
```

This means no JOINs, eventual consistency between tables, and application-level reconciliation — adding complexity without clear benefit for low-frequency fundamentals data.

---

## 4. Data Integrity & Consistency Models

| Database | Consistency | Transactions | Integrity Model | Risk |
|----------|-------------|-------------|-----------------|------|
| **MongoDB Atlas (M0)** | Strong (within replica set) | Multi-document ACID (4.0+), limited on M0 | Schema validation available | Low data loss; 512 MB forces archival |
| **DataStax Astra** | Eventual (default `ONE`); tunable to `QUORUM`/`ALL` | `LOGGED` batches atomic within partition | No foreign keys — application enforced | Higher app complexity |
| **Azure Cosmos DB** | Five levels (Strong → Eventual); default Session | Stored procedures, triggers | Server-side logic available | RU throttling can cause write failures |
| **Google Firestore** | Strong | ACID across documents | Max 500 docs per transaction | Daily operation limits block high-frequency use |

### CAP Theorem Positioning

| Database | CAP Priority | Best For |
|----------|--------------|----------|
| **Cassandra** | AP (Availability + Partition tolerance) | High-frequency trading data, IoT streams |
| **MongoDB** | CP (Consistency + Partition tolerance) | Transactional financial records, user portfolios |
| **Cosmos DB** | Tunable (Session to Strong) | Global distributed ledgers |
| **Firestore** | CP | Real-time trading apps with Firebase |

For stock fundamentals — where **data correctness matters more than write availability** — CP databases (MongoDB, Firestore) are preferable over AP systems (Cassandra).

---

## 5. Read/Write Strategy for Financial Data

### Stock Market Fundamentals (Quarterly/Annual)

Characteristics: Low frequency, high dimensionality, complex relationships.

**Recommended Schema (MongoDB)**:

```javascript
{
  _id: "AAPL_2024_Q1",
  symbol: "AAPL",
  period: "2024-Q1",
  fundamentals: {
    revenue: 90800000000,
    net_income: 23600000000,
    eps: 1.52,
    // ... 100+ metrics
  },
  balance_sheet: { /* nested */ },
  cash_flow: { /* nested */ },
  metadata: {
    last_updated: ISODate(),
    source: "SEC_FILING"
  }
}
```

**Strategy**:
- **Write**: Batch upserts quarterly (low frequency)
- **Read**: Aggregation pipelines for screening (P/E ratios, growth rates, cross-sectional analysis)
- **Index**: Compound index on `{symbol: 1, period: 1}`

### Economic Indicators (Monthly/Quarterly Time Series)

Characteristics: Sparse updates, historical revisions, multi-dimensional.

**Recommended Schema (Cassandra/DataStax Astra)** — only if high-frequency tick data is also needed:

```cql
CREATE TABLE economic_indicators (
    indicator_id text,          -- "GDP_US", "CPI_EU"
    frequency text,             -- "monthly", "quarterly"
    timestamp timestamp,
    value decimal,
    revision_number int,
    PRIMARY KEY ((indicator_id, frequency), timestamp)
) WITH CLUSTERING ORDER BY (timestamp DESC);

CREATE TABLE latest_indicators (
    indicator_id text PRIMARY KEY,
    timestamp timestamp,
    value decimal
);
```

**Strategy**:
- **Write**: Append-only, handle revisions as new rows
- **Read**: Time-range scans, latest value lookups
- **Compaction**: `TimeWindowCompactionStrategy` for time-series optimization

For our use case, **both fundamentals and economic indicators fit well in MongoDB** since the write volumes are low and the query patterns favor flexible aggregation over raw throughput.

---

## 6. Recommendation

### Winner: MongoDB Atlas (M0) → Scale to M10+

**Rationale**:

1. **Data characteristics match**: Fundamentals are low-frequency, high-structure, requiring complex queries (screening, aggregations, cross-sectional analysis). MongoDB's document model maps naturally to SEC filing structures.
2. **Query power**: MongoDB's aggregation framework excels at calculating ratios, growth rates, and multi-dimensional analysis — the core operations for stock screening.
3. **Schema evolution**: Economic indicators change definitions over time (e.g., GDP calculation methodology revisions). MongoDB's flexible schema handles this without migrations.
4. **Free tier is sufficient**: 512 MB accommodates ~5 years of quarterly fundamentals for 3,000+ stocks and ~20 years of monthly economic indicators for major economies.
5. **No overage risk**: M0 hard-stops at limits — no surprise charges.
6. **Clear upgrade path**: M10 ($0.08/hr) when capacity demands it.

### Alternatives Considered

| Database | Verdict | Reason |
|----------|---------|--------|
| **Azure Cosmos DB** | Use only if global multi-region writes with strict SLAs are needed from day one | RU-based pricing is unpredictable for analytical workloads; a complex aggregation can consume 1,000+ RUs per execution |
| **DataStax Astra** | Overkill for low-frequency fundamentals | Eventual consistency adds complexity without benefit; query-driven denormalization is unnecessary overhead |
| **Google Firestore** | Too restrictive | Daily operation limits (50K reads) break backtesting workflows |
| **Couchbase Capella** | Insufficient | 8 GB limiting; N1QL query optimization requires significant tuning |

---

## 7. Implementation Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Data Ingestion Layer                      │
│  (Python/Pandas → Apache Airflow for scheduled ETL)         │
└───────────────────────┬─────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│   MongoDB    │ │   Cassandra  │ │   Parquet    │
│  (Atlas M0)  │ │   (Astra)    │ │   (S3/GCS)   │
│              │ │              │ │              │
│ Fundamentals │ │ High-freq    │ │ Historical   │
│ Economics    │ │ tick data    │ │ archives     │
│ Metadata     │ │ (if needed)  │ │ (cold store) │
└──────────────┘ └──────────────┘ └──────────────┘
        │               │               │
        └───────────────┴───────────────┘
                        │
              ┌─────────▼──────────┐
              │   Analytics Layer  │
              │  (Python/Pandas/   │
              │   Apache Spark)    │
              └────────────────────┘
```

### Cost Projection

| Stage | Data Size | Database | Monthly Cost | Notes |
|-------|-----------|----------|--------------|-------|
| **MVP** | <512 MB | MongoDB M0 | **$0** | Free tier sufficient |
| **Growth** | 5–50 GB | MongoDB M10 | ~$60–$120 | Dedicated cluster |
| **Scale** | 50–500 GB | MongoDB M30 + S3 | ~$200–$400 | Hot/cold architecture |
| **Enterprise** | 500 GB+ | MongoDB M40 + Cassandra | $500+ | Specialized time-series |

---

## 8. Critical Warnings

1. **Cosmos DB "Free Tier" Trap**: The 1,000 RU/s is a provisioned limit. If exceeded, you get charged — not throttled gracefully. A single unoptimized query scanning 25 GB can consume 10,000+ RUs instantly.

2. **DataStax Astra Credit Mechanics**: The $25 credit covers 20M operations. At 1M writes/day (common for financial data), the credit is exhausted in 20 days. Standard rates apply immediately after.

3. **MongoDB M0 Limitations**:
   - No backup/restore (manual export only)
   - Shared resources (noisy neighbor risk)
   - Max 100 connections
   - No overage charges (hard stops at limits)

4. **Cassandra Learning Curve**: If Astra is chosen for time-series, understanding partition key design, compaction strategies (STCS vs LCS vs TWCS), and consistency level tuning is essential.

---

## Bottom Line

For **stock market fundamentals and economic indicators**, MongoDB Atlas (M0) is the right starting point. Its document model matches the semi-structured nature of financial reports, its aggregation framework supports complex screening queries, and the 512 MB free tier is genuinely sufficient for initial development. Only consider DataStax Astra if high-frequency tick data ingestion (millions of records/day) is added later, and Azure Cosmos DB only if global multi-region writes with strict SLAs are required from day one.
