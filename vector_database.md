# Vector Database Selection

## Overview

This document evaluates embedded/local vector databases for QuantLens's semantic search and retrieval-augmented generation (RAG) needs. The primary use case is **local LLM chat** — enabling users to query strategy documentation, backtest results, and financial research using natural language, with an LLM grounded by relevant context retrieved from a local vector store. Secondary use cases include semantic search over strategy libraries, similarity-based strategy discovery, and embedding-based anomaly detection on portfolio metrics.

Unlike the time-series ([ohlcv_database.md](ohlcv_database.md)) and document ([nosql_database.md](nosql_database.md)) storage decisions, this evaluation prioritizes **embedded, serverless operation** — no external database process, no network calls, no infrastructure overhead. The vector database must run in-process alongside the Python backend.

---

## 1. Why a Local Embedded Vector Database

QuantLens runs backtest workloads locally and in self-hosted environments. Adding a managed vector database service introduces latency, cost, and an external dependency for what is fundamentally an in-process operation — embedding a user's query and retrieving the top-k most similar documents from a local corpus.

Requirements:

- **Zero-server architecture**: No separate database process to manage, monitor, or restart.
- **In-process Python integration**: Import as a library, call directly from the FastAPI backend.
- **Persistent storage**: Embeddings survive process restarts without re-indexing.
- **Metadata filtering**: Filter results by strategy type, date range, asset class, or backtest status alongside vector similarity.
- **Low latency**: Sub-10ms retrieval for corpora under 1M vectors (typical for a single-user or small-team deployment).

---

## 2. Candidate Comparison

| Database | Architecture | Local/Embedded Mode | Language | Storage Format | License |
|----------|-------------|---------------------|----------|----------------|---------|
| **LanceDB** | Embedded library | Native — designed for it | Rust core, Python/TS/Rust SDKs | Lance (columnar) | Apache 2.0 |
| **Qdrant** | Client-server | Available (in-memory or on-disk) | Rust | Custom segments | Apache 2.0 |
| **Weaviate** | Client-server | Embedded client available | Go | HNSW + inverted index | BSD-3-Clause |
| **Milvus** | Distributed cluster | Milvus Lite (embedded) available | Go/C++ | Custom segments | Apache 2.0 |
| **ChromaDB** | Embedded library | Native | Python | SQLite + HNSW | Apache 2.0 |

### Feature Comparison

| Feature | LanceDB | Qdrant | Weaviate | Milvus | ChromaDB |
|---------|---------|--------|----------|--------|----------|
| **True embedded (no server process)** | ✅ | ⚠️ Spawns background process | ⚠️ Spawns embedded server | ⚠️ Milvus Lite only | ✅ |
| **Persistent disk storage** | ✅ Lance files | ✅ | ✅ | ✅ | ✅ SQLite |
| **Metadata filtering** | ✅ SQL-like filters | ✅ JSON filters | ✅ GraphQL filters | ✅ Boolean expressions | ✅ Where clauses |
| **Full-text search** | ✅ Tantivy-based | ✅ | ✅ BM25 | ✅ | ❌ |
| **Hybrid search (vector + full-text)** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Automatic embedding** | ✅ Built-in embedding functions | ❌ External | ✅ Vectorizer modules | ❌ External | ✅ Built-in |
| **Multi-modal support** | ✅ Images, text, tables | ❌ | ✅ | ❌ | ❌ |
| **Scalability ceiling** | Cloud/Enterprise tiers | Cluster mode | Cluster mode | Distributed native | Limited |
| **Python install complexity** | `pip install lancedb` | `pip install qdrant-client` | Docker or `pip install weaviate-client` | `pip install pymilvus` | `pip install chromadb` |

---

## 3. Evaluation

### LanceDB — The Embedded-First Choice

LanceDB is built from the ground up as an **embedded vector database**. It runs as an in-process library — no server, no containers, no background processes. Data is stored in the [Lance columnar format](https://github.com/lancedb/lance), optimized for vector operations with zero-copy access and disk-based indexing.

**Strengths**:

| Strength | Detail |
|----------|--------|
| **Zero infrastructure** | `import lancedb` — no Docker, no config, no port management |
| **Lance format** | Columnar, versioned, supports random access — efficient for both vector search and tabular analytics |
| **Built-in embedding functions** | Supports OpenAI, Sentence Transformers, Hugging Face, and local models out of the box |
| **Hybrid search** | Combines vector similarity with full-text search (Tantivy) and SQL-like metadata filters |
| **Versioned data** | Lance format supports time travel and zero-copy updates |
| **Multi-language SDKs** | Python, TypeScript, Rust — aligns with QuantLens's backend and potential CLI tools |

**Example — Local LLM Chat RAG Pipeline**:

```python
import lancedb
from lancedb.embeddings import get_registry

# Use a local embedding model (no API calls)
model = get_registry().get("sentence-transformers").create(
    name="all-MiniLM-L6-v2",
    device="cpu"
)

db = lancedb.connect("./data/lancedb")

# Create table with automatic embedding
table = db.create_table("strategy_docs", schema=LanceModel, mode="overwrite")

# Add documents (embeddings generated automatically)
table.add([
    {"text": "SMA crossover strategy uses 50-day and 200-day moving averages...", "source": "strategies/sma_cross.py"},
    {"text": "Backtest from 2020-01-01 to 2024-12-31 showed Sharpe ratio 1.42...", "source": "results/sma_cross_2024.json"},
])

# Query with natural language
results = table.search("Which strategy had the best risk-adjusted returns?") \
    .limit(5) \
    .to_pandas()
```

**Limitations**:

| Limitation | Impact |
|------------|--------|
| **Single-writer** | Only one process can write at a time — acceptable for QuantLens (single backend process) |
| **No built-in replication** | Local only; no automatic sync across machines — fine for local/self-hosted deployment |
| **Younger ecosystem** | Fewer community integrations than Qdrant/Weaviate — mitigated by LangChain and LlamaIndex support |

### Qdrant — The Feature-Rich Alternative

Qdrant is a Rust-based vector search engine designed as a **client-server system** with an embedded mode available via `qdrant-client`.

- **Embedded mode** runs an in-process Qdrant instance but still initializes a gRPC server internally — heavier than LanceDB's pure library approach.
- Strong filtering capabilities with payload indexes (JSON metadata).
- Mature, well-documented API with rich query options (batch search, recommendation API, grouping).
- **Best for**: Teams that may later scale to a dedicated Qdrant server but want to start embedded.

### Weaviate — The Vectorizer-Integrated Option

Weaviate is a Go-based vector database with strong **local vectorization integrations** — it can run embedding models (Transformers, CLIP) within its own modules.

- **Embedded client** spawns a Weaviate server process in the background — not truly in-process.
- GraphQL-based query API adds a learning curve for simple retrieval operations.
- Excellent for multi-modal search (text + images) if QuantLens adds chart/screenshot analysis.
- **Best for**: Applications needing built-in vectorization pipelines with a full-featured query language.

### Milvus — The Distributed Heavyweight

Milvus is designed for **large-scale, cloud-native, distributed deployments**. Milvus Lite provides an embedded option, but:

- The distributed architecture (etcd, MinIO, Pulsar dependencies in full mode) makes it overly complex for single-machine use.
- Milvus Lite strips many features to fit the embedded model — defeating the purpose of choosing Milvus.
- Resource-intensive even in lite mode compared to LanceDB or ChromaDB.
- **Best for**: Enterprise deployments managing billions of vectors across clusters.

### ChromaDB — The Simple Prototyping Option

ChromaDB is a Python-native embedded vector database, often the first choice for LLM prototyping.

- Truly embedded (SQLite + HNSW index), simple API.
- No full-text search or hybrid search — limiting for production RAG pipelines.
- Performance degrades beyond ~1M vectors without careful tuning.
- **Best for**: Quick prototyping and tutorials — not production RAG systems.

---

## 4. Use Cases for QuantLens

| Use Case | Description | Why LanceDB |
|----------|-------------|-------------|
| **Local LLM chat** | RAG pipeline grounding LLM responses with strategy docs, backtest results, and financial research | Embedded operation, built-in embedding functions, hybrid search for combining semantic similarity with metadata filters |
| **Expert analyses & company news** | Store and retrieve analyst reports, earnings call transcripts, and financial news for LLM-powered analysis | Semantic search over text-heavy financial content with metadata filtering by ticker, date, and source |
| **LLM financial modeling** | Feed fundamentals + price patterns to LLM for DCF models, risk analysis, and valuation | Retrieve similar historical patterns via vector similarity, combine with structured data from DuckDB |
| **Strategy similarity search** | Find strategies with similar logic, parameters, or performance characteristics | Vector search over strategy embeddings with metadata filtering by asset class, timeframe, or Sharpe ratio |
| **Documentation search** | Semantic search over NautilusTrader docs, QuantLens guides, and user notes | Full-text + vector hybrid search for precise retrieval |
| **Anomaly detection** | Flag unusual portfolio metrics or backtest results by comparing against historical embeddings | Lance format's columnar storage enables efficient batch comparisons |

---

## 5. Recommendation

### Winner: LanceDB

**Rationale**:

1. **True embedded architecture.** LanceDB runs as an in-process library — `import lancedb` and connect to a local directory. No Docker containers, no background servers, no port conflicts with existing services. This aligns with QuantLens's local-first design philosophy.

2. **Lance columnar format.** Unlike databases that store vectors in opaque binary formats, Lance provides versioned, columnar storage with zero-copy reads. This means vector data can also be queried as tabular data (Pandas/Polars DataFrames) — useful for combining vector search results with quantitative analysis.

3. **Built-in embedding functions.** LanceDB natively supports local embedding models (Sentence Transformers, Hugging Face) without external API calls. For a local LLM chat feature, this means the entire pipeline — embedding, storage, retrieval — runs without network dependencies.

4. **Hybrid search.** Combining vector similarity with full-text search (Tantivy) and metadata filters in a single query is essential for RAG quality. Searching for "momentum strategy with high Sharpe" should leverage both semantic understanding and structured metadata.

5. **Python-native workflow.** `pip install lancedb` — no system dependencies, no compilation steps. Results return as Pandas DataFrames or Arrow tables, integrating directly with the existing data pipeline (Polars, NautilusTrader's `ParquetDataCatalog`).

6. **Upgrade path.** LanceDB Cloud and Enterprise tiers use the same Lance format and API. If QuantLens scales beyond local deployment, migration requires changing the connection string, not the application code.

### Alternatives Considered

| Database | Verdict | Reason |
|----------|---------|--------|
| **Qdrant** | Strong alternative if dedicated server is needed later | Embedded mode still spawns internal server; heavier than LanceDB for pure local use; excellent choice if QuantLens adds a standalone vector search service |
| **Weaviate** | Consider if multi-modal search becomes critical | Built-in vectorization modules are powerful but the embedded client is not truly in-process; GraphQL API adds unnecessary complexity for simple RAG retrieval |
| **Milvus** | Overly complex for local use | Distributed architecture (etcd, MinIO, Pulsar) is designed for billion-scale deployments; Milvus Lite sacrifices too many features to justify the complexity |
| **ChromaDB** | Too limited for production | No full-text search, no hybrid queries, performance ceiling at ~1M vectors; suitable for prototyping but not production RAG |

---

## 6. Integration Architecture

### Strategy & Documentation RAG Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                    User Query (Natural Language)             │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Embedding Model                          │
│            (Sentence Transformers — local, no API)          │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                       LanceDB                               │
│                  (Embedded, ./data/lancedb)                  │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │ strategy_docs│  │backtest_results│ │ research_notes│     │
│  │   (vectors)  │  │   (vectors)  │  │   (vectors)  │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                             │
│  ┌──────────────┐  ┌──────────────┐                        │
│  │expert_analyses│ │ company_news │                        │
│  │   (vectors)  │  │   (vectors)  │                        │
│  └──────────────┘  └──────────────┘                        │
│                                                             │
│  Hybrid Search: vector similarity + full-text + metadata    │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    Local LLM / API LLM                      │
│          (Context-augmented response generation)            │
└─────────────────────────────────────────────────────────────┘
```

### Hybrid DuckDB + LanceDB Architecture for LLM Financial Analysis

When feeding fundamentals and price patterns to an LLM for financial modeling, vector search adds value for **semantic retrieval of patterns, expert analyses, and similar historical setups** — a fundamentally different operation from the structured queries that DuckDB handles.

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Structured Storage** | DuckDB (see [nosql_database.md](nosql_database.md)) | Fundamentals, economic indicators, screening queries |
| **Time-Series Storage** | QuestDB (see [ohlcv_database.md](ohlcv_database.md)) | OHLCV prices, tick data, market data |
| **Vector Store** | LanceDB | Expert analyses, company news, pattern embeddings, semantic search |
| **Orchestration** | Python (LangChain/LlamaIndex) | Retrieve structured + semantic context → LLM prompt |

**Why vector search makes sense here (but not for raw fundamentals)**:

- **Pattern matching**: "Find stocks with price action similar to current NVDA setup" → Embed 30-day price curves, query by vector similarity
- **Expert analysis retrieval**: "What did analysts say about SaaS companies with declining growth?" → Embed analyst reports, retrieve semantically similar analyses
- **Fundamental clustering**: "Companies with similar margin compression trajectories" → Embed normalized fundamental sequences as text descriptions

**When to skip vector search**: If the LLM use case is purely "query latest AAPL fundamentals" without semantic pattern matching, DuckDB's full-text search (FTS) extension suffices. Add vectors only when you need "find similar patterns" semantics.

**Why LanceDB for this hybrid workflow**: Since QuantLens already uses DuckDB for structured data, LanceDB's Arrow-native format eliminates serialization overhead between the two databases. Both run embedded — no Docker networking complexity.

```python
import duckdb
import lancedb

# 1. Structured data in DuckDB
con = duckdb.connect('fundamentals.db')
fundamentals = con.execute("""
    SELECT ticker, revenue, net_income, eps, pe_ratio
    FROM fundamentals
    WHERE ticker = 'NVDA' AND period >= '2023-Q1'
""").fetchdf()

# 2. Semantic retrieval from LanceDB (expert analyses, similar patterns)
db = lancedb.connect("./data/lancedb")
analyses_table = db.open_table("expert_analyses")

# Find similar expert analyses for context
similar_analyses = analyses_table.search(
    "NVDA revenue growth deceleration data center demand"
).metric("cosine").limit(5).to_pandas()

# 3. Build LLM prompt with both structured + semantic context
prompt = f"""
Based on these expert analyses: {similar_analyses['text'].tolist()}
And current fundamentals: {fundamentals.to_json()}
Build a DCF model and identify key risks.
"""
```

### Data Flow

1. **Ingestion**: Strategy files, backtest results (JSON/Parquet), expert analyses, company news, and documentation are chunked, embedded (locally via Sentence Transformers), and stored in LanceDB tables.
2. **Query**: User's natural language question is embedded using the same model, then searched against LanceDB with optional metadata filters (date range, strategy type, asset class, ticker).
3. **Retrieval**: Top-k results are returned as a Pandas DataFrame with text content, similarity scores, and metadata. Structured data is retrieved from DuckDB via SQL.
4. **Generation**: Retrieved context (semantic + structured) is passed to the LLM (local or API-based) as grounding material for the response.

---

## Bottom Line

For **local LLM chat, semantic search, and LLM-powered financial analysis**, LanceDB is the right choice. Its embedded architecture eliminates infrastructure overhead, its Lance format bridges vector search and tabular analytics, and its built-in embedding functions enable a fully local RAG pipeline with no external dependencies. The hybrid DuckDB + LanceDB architecture — with DuckDB handling structured fundamentals and LanceDB handling expert analyses, company news, and pattern embeddings — provides the optimal split between SQL analytics and semantic retrieval, with both databases running embedded and sharing data via Arrow tables. Qdrant and Weaviate are strong alternatives if QuantLens later needs a dedicated vector search service or multi-modal capabilities, but for the current local-first architecture, LanceDB's in-process library approach is the simplest and most efficient path.
