"""
Fundamentals Database Benchmark Suite

Benchmarks 13 databases for stock fundamentals and economic data workloads:
  - DuckDB, SQLite (embedded)
  - PostgreSQL, MariaDB, MySQL, TimescaleDB (relational / Docker)
  - MongoDB, FerretDB (document / Docker)
  - Cassandra, ScyllaDB (wide-column / Docker)
  - Redis (key-value / Docker)
  - ClickHouse (columnar / Docker)
  - RavenDB (document / Docker)

Generates synthetic stock fundamentals and economic indicator data, then runs:
  1. Single-query workload  (one query type at a time)
  2. Multi-query workload   (mixed query types in sequence)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator

# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------

SYMBOLS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK.B",
    "JPM", "V", "UNH", "HD", "PG", "MA", "DIS", "PYPL", "NFLX", "ADBE",
    "CRM", "INTC", "CSCO", "PEP", "AVGO", "COST", "QCOM", "TXN", "TMUS",
    "AMD", "AMAT", "SBUX",
]

PERIODS = [f"{y}-Q{q}" for y in range(2018, 2025) for q in range(1, 5)]

ECON_INDICATORS = [
    "GDP_US", "CPI_US", "UNRATE_US", "GDP_EU", "CPI_EU", "FEDFUNDS",
    "T10Y2Y", "INDPRO", "PAYEMS", "PCE_US",
]

ECON_FREQUENCIES = ["monthly", "quarterly"]


def _rand_float(lo: float, hi: float) -> float:
    return round(random.uniform(lo, hi), 4)


def generate_fundamentals(n_symbols: int = 30, n_periods: int = 28) -> list[dict]:
    """Return a list of fundamental records (symbol x period)."""
    random.seed(42)
    symbols = SYMBOLS[:n_symbols]
    periods = PERIODS[:n_periods]
    rows: list[dict] = []
    for sym in symbols:
        base_rev = _rand_float(1e9, 5e11)
        for period in periods:
            revenue = base_rev * _rand_float(0.9, 1.15)
            net_income = revenue * _rand_float(0.05, 0.25)
            eps = _rand_float(0.5, 20.0)
            pe_ratio = _rand_float(5.0, 80.0)
            rows.append(
                {
                    "symbol": sym,
                    "period": period,
                    "revenue": round(revenue, 2),
                    "net_income": round(net_income, 2),
                    "eps": eps,
                    "pe_ratio": pe_ratio,
                    "book_value": _rand_float(10, 500),
                    "dividend_yield": _rand_float(0, 5),
                    "debt_to_equity": _rand_float(0, 3),
                    "roe": _rand_float(-0.1, 0.5),
                    "roa": _rand_float(-0.05, 0.25),
                    "current_ratio": _rand_float(0.5, 4.0),
                    "gross_margin": _rand_float(0.1, 0.9),
                    "operating_margin": _rand_float(-0.1, 0.5),
                    "free_cash_flow": _rand_float(-1e9, 5e10),
                    "market_cap": _rand_float(1e9, 3e12),
                    "balance_sheet": json.dumps(
                        {
                            "total_assets": _rand_float(1e9, 5e11),
                            "total_liabilities": _rand_float(5e8, 3e11),
                            "equity": _rand_float(1e8, 2e11),
                        }
                    ),
                    "cash_flow": json.dumps(
                        {
                            "operating": _rand_float(-1e9, 5e10),
                            "investing": _rand_float(-5e10, 0),
                            "financing": _rand_float(-3e10, 3e10),
                        }
                    ),
                }
            )
    return rows


def generate_economic_data(n_indicators: int = 10, n_months: int = 84) -> list[dict]:
    """Return a list of economic indicator records."""
    random.seed(43)
    indicators = ECON_INDICATORS[:n_indicators]
    rows: list[dict] = []
    for ind in indicators:
        freq = random.choice(ECON_FREQUENCIES)
        base_val = _rand_float(50, 30000)
        for m in range(n_months):
            year = 2018 + m // 12
            month = 1 + m % 12
            ts = f"{year}-{month:02d}-01T00:00:00"
            value = base_val * _rand_float(0.95, 1.05)
            for rev in range(1, random.randint(1, 3) + 1):
                rows.append(
                    {
                        "indicator_id": ind,
                        "frequency": freq,
                        "timestamp": ts,
                        "value": round(value * _rand_float(0.999, 1.001), 4),
                        "revision_number": rev,
                    }
                )
    return rows


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------


@dataclass
class BenchResult:
    db_name: str
    operation: str
    elapsed_ms: float
    row_count: int = 0
    error: str | None = None


@dataclass
class BenchSuite:
    results: list[BenchResult] = field(default_factory=list)

    def add(self, r: BenchResult) -> None:
        self.results.append(r)


@contextmanager
def _timer() -> Generator[list[float], None, None]:
    container: list[float] = []
    t0 = time.perf_counter()
    yield container
    container.append((time.perf_counter() - t0) * 1000)


# ---------------------------------------------------------------------------
# Database adapters
# ---------------------------------------------------------------------------


class DBAdapter:
    """Base class — each subclass connects to one DB engine."""

    name: str = "base"

    def setup(self, fundamentals: list[dict], economic: list[dict]) -> None:
        raise NotImplementedError

    def single_query_fundamentals(self) -> int:
        raise NotImplementedError

    def single_query_economic(self) -> int:
        raise NotImplementedError

    def multi_query_workload(self) -> int:
        raise NotImplementedError

    def teardown(self) -> None:
        pass


# ---- DuckDB ----------------------------------------------------------------

class DuckDBAdapter(DBAdapter):
    name = "DuckDB"

    def setup(self, fundamentals, economic):
        import duckdb

        self.con = duckdb.connect(":memory:")
        self.con.execute("""
            CREATE TABLE fundamentals (
                symbol VARCHAR, period VARCHAR, revenue DOUBLE,
                net_income DOUBLE, eps DOUBLE, pe_ratio DOUBLE,
                book_value DOUBLE, dividend_yield DOUBLE,
                debt_to_equity DOUBLE, roe DOUBLE, roa DOUBLE,
                current_ratio DOUBLE, gross_margin DOUBLE,
                operating_margin DOUBLE, free_cash_flow DOUBLE,
                market_cap DOUBLE, balance_sheet JSON, cash_flow JSON,
                PRIMARY KEY (symbol, period)
            )
        """)
        self.con.execute("""
            CREATE TABLE economic_indicators (
                indicator_id VARCHAR, frequency VARCHAR,
                timestamp TIMESTAMP, value DOUBLE,
                revision_number INTEGER,
                PRIMARY KEY (indicator_id, frequency, timestamp, revision_number)
            )
        """)
        self.con.executemany(
            "INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [tuple(r.values()) for r in fundamentals],
        )
        self.con.executemany(
            "INSERT INTO economic_indicators VALUES (?,?,?,?,?)",
            [tuple(r.values()) for r in economic],
        )

    def single_query_fundamentals(self):
        return len(
            self.con.execute(
                "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
            ).fetchall()
        )

    def single_query_economic(self):
        return len(
            self.con.execute("""
                SELECT indicator_id, timestamp, value FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                    ) AS rn FROM economic_indicators
                ) WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
            """).fetchall()
        )

    def multi_query_workload(self):
        total = 0
        total += len(
            self.con.execute(
                "SELECT symbol, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe FROM fundamentals GROUP BY symbol ORDER BY avg_pe"
            ).fetchall()
        )
        total += len(
            self.con.execute(
                "SELECT symbol, period, revenue, LAG(revenue,4) OVER (PARTITION BY symbol ORDER BY period) AS rev_yoy FROM fundamentals"
            ).fetchall()
        )
        total += len(
            self.con.execute(
                "SELECT indicator_id, AVG(value) AS avg_val FROM economic_indicators GROUP BY indicator_id"
            ).fetchall()
        )
        return total

    def teardown(self):
        self.con.close()


# ---- SQLite -----------------------------------------------------------------

class SQLiteAdapter(DBAdapter):
    name = "SQLite"

    def setup(self, fundamentals, economic):
        import sqlite3

        self.con = sqlite3.connect(":memory:")
        self.con.execute("""
            CREATE TABLE fundamentals (
                symbol TEXT, period TEXT, revenue REAL,
                net_income REAL, eps REAL, pe_ratio REAL,
                book_value REAL, dividend_yield REAL,
                debt_to_equity REAL, roe REAL, roa REAL,
                current_ratio REAL, gross_margin REAL,
                operating_margin REAL, free_cash_flow REAL,
                market_cap REAL, balance_sheet TEXT, cash_flow TEXT,
                PRIMARY KEY (symbol, period)
            )
        """)
        self.con.execute("""
            CREATE TABLE economic_indicators (
                indicator_id TEXT, frequency TEXT,
                timestamp TEXT, value REAL,
                revision_number INTEGER,
                PRIMARY KEY (indicator_id, frequency, timestamp, revision_number)
            )
        """)
        self.con.executemany(
            "INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [tuple(r.values()) for r in fundamentals],
        )
        self.con.executemany(
            "INSERT INTO economic_indicators VALUES (?,?,?,?,?)",
            [tuple(r.values()) for r in economic],
        )
        self.con.commit()

    def single_query_fundamentals(self):
        return len(
            self.con.execute(
                "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
            ).fetchall()
        )

    def single_query_economic(self):
        return len(
            self.con.execute("""
                SELECT indicator_id, timestamp, value FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                    ) AS rn FROM economic_indicators
                ) WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
            """).fetchall()
        )

    def multi_query_workload(self):
        total = 0
        total += len(
            self.con.execute(
                "SELECT symbol, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe FROM fundamentals GROUP BY symbol ORDER BY avg_pe"
            ).fetchall()
        )
        total += len(
            self.con.execute(
                "SELECT symbol, period, revenue, LAG(revenue,4) OVER (PARTITION BY symbol ORDER BY period) AS rev_yoy FROM fundamentals"
            ).fetchall()
        )
        total += len(
            self.con.execute(
                "SELECT indicator_id, AVG(value) AS avg_val FROM economic_indicators GROUP BY indicator_id"
            ).fetchall()
        )
        return total

    def teardown(self):
        self.con.close()


# ---- PostgreSQL -------------------------------------------------------------

class PostgreSQLAdapter(DBAdapter):
    name = "PostgreSQL"

    def setup(self, fundamentals, economic):
        import psycopg2

        self.con = psycopg2.connect(
            host="localhost", port=5432, user="bench", password="bench", dbname="bench"
        )
        self.con.autocommit = True
        cur = self.con.cursor()
        cur.execute("DROP TABLE IF EXISTS fundamentals")
        cur.execute("DROP TABLE IF EXISTS economic_indicators")
        cur.execute("""
            CREATE TABLE fundamentals (
                symbol TEXT, period TEXT, revenue DOUBLE PRECISION,
                net_income DOUBLE PRECISION, eps DOUBLE PRECISION, pe_ratio DOUBLE PRECISION,
                book_value DOUBLE PRECISION, dividend_yield DOUBLE PRECISION,
                debt_to_equity DOUBLE PRECISION, roe DOUBLE PRECISION, roa DOUBLE PRECISION,
                current_ratio DOUBLE PRECISION, gross_margin DOUBLE PRECISION,
                operating_margin DOUBLE PRECISION, free_cash_flow DOUBLE PRECISION,
                market_cap DOUBLE PRECISION, balance_sheet TEXT, cash_flow TEXT,
                PRIMARY KEY (symbol, period)
            )
        """)
        cur.execute("""
            CREATE TABLE economic_indicators (
                indicator_id TEXT, frequency TEXT,
                timestamp TIMESTAMP, value DOUBLE PRECISION,
                revision_number INTEGER,
                PRIMARY KEY (indicator_id, frequency, timestamp, revision_number)
            )
        """)
        for r in fundamentals:
            cur.execute(
                "INSERT INTO fundamentals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(r.values()),
            )
        for r in economic:
            cur.execute(
                "INSERT INTO economic_indicators VALUES (%s,%s,%s,%s,%s)",
                tuple(r.values()),
            )

    def single_query_fundamentals(self):
        cur = self.con.cursor()
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        return len(cur.fetchall())

    def single_query_economic(self):
        cur = self.con.cursor()
        cur.execute("""
            SELECT indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM economic_indicators
            ) sub WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
        """)
        return len(cur.fetchall())

    def multi_query_workload(self):
        cur = self.con.cursor()
        total = 0
        cur.execute(
            "SELECT symbol, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe FROM fundamentals GROUP BY symbol ORDER BY avg_pe"
        )
        total += len(cur.fetchall())
        cur.execute(
            "SELECT symbol, period, revenue, LAG(revenue,4) OVER (PARTITION BY symbol ORDER BY period) AS rev_yoy FROM fundamentals"
        )
        total += len(cur.fetchall())
        cur.execute(
            "SELECT indicator_id, AVG(value) AS avg_val FROM economic_indicators GROUP BY indicator_id"
        )
        total += len(cur.fetchall())
        return total

    def teardown(self):
        self.con.close()


# ---- MySQL ------------------------------------------------------------------

class MySQLAdapter(DBAdapter):
    name = "MySQL"

    def setup(self, fundamentals, economic):
        import mysql.connector

        self.con = mysql.connector.connect(
            host="127.0.0.1", port=3306, user="bench", password="bench", database="bench"
        )
        cur = self.con.cursor()
        cur.execute("DROP TABLE IF EXISTS fundamentals")
        cur.execute("DROP TABLE IF EXISTS economic_indicators")
        cur.execute("""
            CREATE TABLE fundamentals (
                symbol VARCHAR(20), period VARCHAR(20), revenue DOUBLE,
                net_income DOUBLE, eps DOUBLE, pe_ratio DOUBLE,
                book_value DOUBLE, dividend_yield DOUBLE,
                debt_to_equity DOUBLE, roe DOUBLE, roa DOUBLE,
                current_ratio DOUBLE, gross_margin DOUBLE,
                operating_margin DOUBLE, free_cash_flow DOUBLE,
                market_cap DOUBLE, balance_sheet TEXT, cash_flow TEXT,
                PRIMARY KEY (symbol, period)
            )
        """)
        cur.execute("""
            CREATE TABLE economic_indicators (
                indicator_id VARCHAR(30), frequency VARCHAR(20),
                timestamp DATETIME, value DOUBLE,
                revision_number INTEGER,
                PRIMARY KEY (indicator_id, frequency, timestamp, revision_number)
            )
        """)
        self.con.commit()
        for r in fundamentals:
            cur.execute(
                "INSERT INTO fundamentals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(r.values()),
            )
        for r in economic:
            cur.execute(
                "INSERT INTO economic_indicators VALUES (%s,%s,%s,%s,%s)",
                tuple(r.values()),
            )
        self.con.commit()

    def single_query_fundamentals(self):
        cur = self.con.cursor()
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        return len(cur.fetchall())

    def single_query_economic(self):
        cur = self.con.cursor()
        cur.execute("""
            SELECT indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM economic_indicators
            ) sub WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
        """)
        return len(cur.fetchall())

    def multi_query_workload(self):
        cur = self.con.cursor()
        total = 0
        cur.execute(
            "SELECT symbol, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe FROM fundamentals GROUP BY symbol ORDER BY avg_pe"
        )
        total += len(cur.fetchall())
        cur.execute(
            "SELECT symbol, period, revenue, LAG(revenue,4) OVER (PARTITION BY symbol ORDER BY period) AS rev_yoy FROM fundamentals"
        )
        total += len(cur.fetchall())
        cur.execute(
            "SELECT indicator_id, AVG(value) AS avg_val FROM economic_indicators GROUP BY indicator_id"
        )
        total += len(cur.fetchall())
        return total

    def teardown(self):
        self.con.close()


# ---- MariaDB ----------------------------------------------------------------

class MariaDBAdapter(DBAdapter):
    name = "MariaDB"

    def setup(self, fundamentals, economic):
        import mysql.connector

        self.con = mysql.connector.connect(
            host="127.0.0.1", port=3307, user="bench", password="bench", database="bench"
        )
        cur = self.con.cursor()
        cur.execute("DROP TABLE IF EXISTS fundamentals")
        cur.execute("DROP TABLE IF EXISTS economic_indicators")
        cur.execute("""
            CREATE TABLE fundamentals (
                symbol VARCHAR(20), period VARCHAR(20), revenue DOUBLE,
                net_income DOUBLE, eps DOUBLE, pe_ratio DOUBLE,
                book_value DOUBLE, dividend_yield DOUBLE,
                debt_to_equity DOUBLE, roe DOUBLE, roa DOUBLE,
                current_ratio DOUBLE, gross_margin DOUBLE,
                operating_margin DOUBLE, free_cash_flow DOUBLE,
                market_cap DOUBLE, balance_sheet TEXT, cash_flow TEXT,
                PRIMARY KEY (symbol, period)
            )
        """)
        cur.execute("""
            CREATE TABLE economic_indicators (
                indicator_id VARCHAR(30), frequency VARCHAR(20),
                timestamp DATETIME, value DOUBLE,
                revision_number INTEGER,
                PRIMARY KEY (indicator_id, frequency, timestamp, revision_number)
            )
        """)
        self.con.commit()
        for r in fundamentals:
            cur.execute(
                "INSERT INTO fundamentals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(r.values()),
            )
        for r in economic:
            cur.execute(
                "INSERT INTO economic_indicators VALUES (%s,%s,%s,%s,%s)",
                tuple(r.values()),
            )
        self.con.commit()

    def single_query_fundamentals(self):
        cur = self.con.cursor()
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        return len(cur.fetchall())

    def single_query_economic(self):
        cur = self.con.cursor()
        cur.execute("""
            SELECT indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM economic_indicators
            ) sub WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
        """)
        return len(cur.fetchall())

    def multi_query_workload(self):
        cur = self.con.cursor()
        total = 0
        cur.execute(
            "SELECT symbol, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe FROM fundamentals GROUP BY symbol ORDER BY avg_pe"
        )
        total += len(cur.fetchall())
        cur.execute(
            "SELECT symbol, period, revenue, LAG(revenue,4) OVER (PARTITION BY symbol ORDER BY period) AS rev_yoy FROM fundamentals"
        )
        total += len(cur.fetchall())
        cur.execute(
            "SELECT indicator_id, AVG(value) AS avg_val FROM economic_indicators GROUP BY indicator_id"
        )
        total += len(cur.fetchall())
        return total

    def teardown(self):
        self.con.close()


# ---- MongoDB ----------------------------------------------------------------

class MongoDBAdapter(DBAdapter):
    name = "MongoDB"

    def setup(self, fundamentals, economic):
        from pymongo import MongoClient

        self.client = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=5000)
        self.db = self.client["bench"]
        self.db.drop_collection("fundamentals")
        self.db.drop_collection("economic_indicators")
        self.db["fundamentals"].insert_many([dict(r) for r in fundamentals])
        self.db["economic_indicators"].insert_many([dict(r) for r in economic])
        self.db["fundamentals"].create_index([("symbol", 1), ("period", 1)], unique=True)
        self.db["economic_indicators"].create_index(
            [("indicator_id", 1), ("timestamp", 1), ("revision_number", -1)]
        )

    def single_query_fundamentals(self):
        return len(
            list(
                self.db["fundamentals"].find(
                    {"pe_ratio": {"$lt": 20}, "revenue": {"$gt": 1e10}},
                    {"symbol": 1, "period": 1, "revenue": 1, "eps": 1, "pe_ratio": 1},
                ).sort("pe_ratio", 1)
            )
        )

    def single_query_economic(self):
        pipeline = [
            {"$sort": {"revision_number": -1}},
            {
                "$group": {
                    "_id": {"indicator_id": "$indicator_id", "timestamp": "$timestamp"},
                    "value": {"$first": "$value"},
                    "indicator_id": {"$first": "$indicator_id"},
                    "timestamp": {"$first": "$timestamp"},
                }
            },
            {"$sort": {"timestamp": -1}},
            {"$limit": 100},
            {"$project": {"_id": 0, "indicator_id": 1, "timestamp": 1, "value": 1}},
        ]
        return len(list(self.db["economic_indicators"].aggregate(pipeline)))

    def multi_query_workload(self):
        total = 0
        total += len(
            list(
                self.db["fundamentals"].aggregate(
                    [
                        {
                            "$group": {
                                "_id": "$symbol",
                                "avg_eps": {"$avg": "$eps"},
                                "avg_pe": {"$avg": "$pe_ratio"},
                            }
                        },
                        {"$sort": {"avg_pe": 1}},
                    ]
                )
            )
        )
        total += len(list(self.db["fundamentals"].find({}, {"symbol": 1, "period": 1, "revenue": 1})))
        total += len(
            list(
                self.db["economic_indicators"].aggregate(
                    [{"$group": {"_id": "$indicator_id", "avg_val": {"$avg": "$value"}}}]
                )
            )
        )
        return total

    def teardown(self):
        self.client.close()


# ---- FerretDB (MongoDB-compatible, backed by PostgreSQL) --------------------

class FerretDBAdapter(DBAdapter):
    name = "FerretDB"

    def setup(self, fundamentals, economic):
        from pymongo import MongoClient

        self.client = MongoClient("mongodb://ferretdb:ferretdb@localhost:27018", serverSelectionTimeoutMS=5000)
        self.db = self.client["bench"]
        self.db.drop_collection("fundamentals")
        self.db.drop_collection("economic_indicators")
        self.db["fundamentals"].insert_many([dict(r) for r in fundamentals])
        self.db["economic_indicators"].insert_many([dict(r) for r in economic])

    def single_query_fundamentals(self):
        return len(
            list(
                self.db["fundamentals"].find(
                    {"pe_ratio": {"$lt": 20}, "revenue": {"$gt": 1e10}},
                    {"symbol": 1, "period": 1, "revenue": 1, "eps": 1, "pe_ratio": 1},
                ).sort("pe_ratio", 1)
            )
        )

    def single_query_economic(self):
        pipeline = [
            {"$sort": {"revision_number": -1}},
            {
                "$group": {
                    "_id": {"indicator_id": "$indicator_id", "timestamp": "$timestamp"},
                    "value": {"$first": "$value"},
                    "indicator_id": {"$first": "$indicator_id"},
                    "timestamp": {"$first": "$timestamp"},
                }
            },
            {"$sort": {"timestamp": -1}},
            {"$limit": 100},
            {"$project": {"_id": 0, "indicator_id": 1, "timestamp": 1, "value": 1}},
        ]
        return len(list(self.db["economic_indicators"].aggregate(pipeline)))

    def multi_query_workload(self):
        total = 0
        total += len(
            list(
                self.db["fundamentals"].aggregate(
                    [
                        {
                            "$group": {
                                "_id": "$symbol",
                                "avg_eps": {"$avg": "$eps"},
                                "avg_pe": {"$avg": "$pe_ratio"},
                            }
                        },
                        {"$sort": {"avg_pe": 1}},
                    ]
                )
            )
        )
        total += len(list(self.db["fundamentals"].find({}, {"symbol": 1, "period": 1, "revenue": 1})))
        total += len(
            list(
                self.db["economic_indicators"].aggregate(
                    [{"$group": {"_id": "$indicator_id", "avg_val": {"$avg": "$value"}}}]
                )
            )
        )
        return total

    def teardown(self):
        self.client.close()


# ---- Cassandra --------------------------------------------------------------

class CassandraAdapter(DBAdapter):
    name = "Cassandra"

    def setup(self, fundamentals, economic):
        from cassandra.cluster import Cluster

        self.cluster = Cluster(["127.0.0.1"], port=9042)
        self.session = self.cluster.connect()
        self.session.execute("""
            CREATE KEYSPACE IF NOT EXISTS bench
            WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}
        """)
        self.session.set_keyspace("bench")
        self.session.execute("DROP TABLE IF EXISTS fundamentals")
        self.session.execute("DROP TABLE IF EXISTS economic_indicators")
        self.session.execute("""
            CREATE TABLE fundamentals (
                symbol text, period text, revenue double,
                net_income double, eps double, pe_ratio double,
                book_value double, dividend_yield double,
                debt_to_equity double, roe double, roa double,
                current_ratio double, gross_margin double,
                operating_margin double, free_cash_flow double,
                market_cap double, balance_sheet text, cash_flow text,
                PRIMARY KEY (symbol, period)
            )
        """)
        self.session.execute("""
            CREATE TABLE economic_indicators (
                indicator_id text, frequency text,
                timestamp timestamp, value double,
                revision_number int,
                PRIMARY KEY ((indicator_id), timestamp, revision_number)
            ) WITH CLUSTERING ORDER BY (timestamp DESC, revision_number DESC)
        """)
        fund_stmt = self.session.prepare(
            "INSERT INTO fundamentals (symbol,period,revenue,net_income,eps,pe_ratio,"
            "book_value,dividend_yield,debt_to_equity,roe,roa,current_ratio,"
            "gross_margin,operating_margin,free_cash_flow,market_cap,balance_sheet,cash_flow) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        econ_stmt = self.session.prepare(
            "INSERT INTO economic_indicators (indicator_id,frequency,timestamp,value,revision_number) "
            "VALUES (?,?,?,?,?)"
        )
        for r in fundamentals:
            self.session.execute(fund_stmt, list(r.values()))
        for r in economic:
            self.session.execute(econ_stmt, list(r.values()))

    def single_query_fundamentals(self):
        rows = self.session.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals"
        )
        return len([r for r in rows if r.pe_ratio < 20 and r.revenue > 1e10])

    def single_query_economic(self):
        rows = self.session.execute(
            "SELECT indicator_id, timestamp, value, revision_number FROM economic_indicators LIMIT 100"
        )
        return len(list(rows))

    def multi_query_workload(self):
        total = 0
        rows = list(self.session.execute("SELECT symbol, eps, pe_ratio FROM fundamentals"))
        total += len(rows)
        rows = list(
            self.session.execute("SELECT symbol, period, revenue FROM fundamentals")
        )
        total += len(rows)
        rows = list(
            self.session.execute("SELECT indicator_id, value FROM economic_indicators")
        )
        total += len(rows)
        return total

    def teardown(self):
        self.cluster.shutdown()


# ---- ScyllaDB (Cassandra-compatible) ----------------------------------------

class ScyllaDBAdapter(DBAdapter):
    name = "ScyllaDB"

    def setup(self, fundamentals, economic):
        from cassandra.cluster import Cluster

        self.cluster = Cluster(["127.0.0.1"], port=9043)
        self.session = self.cluster.connect()
        self.session.execute("""
            CREATE KEYSPACE IF NOT EXISTS bench
            WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 1}
        """)
        self.session.set_keyspace("bench")
        self.session.execute("DROP TABLE IF EXISTS fundamentals")
        self.session.execute("DROP TABLE IF EXISTS economic_indicators")
        self.session.execute("""
            CREATE TABLE fundamentals (
                symbol text, period text, revenue double,
                net_income double, eps double, pe_ratio double,
                book_value double, dividend_yield double,
                debt_to_equity double, roe double, roa double,
                current_ratio double, gross_margin double,
                operating_margin double, free_cash_flow double,
                market_cap double, balance_sheet text, cash_flow text,
                PRIMARY KEY (symbol, period)
            )
        """)
        self.session.execute("""
            CREATE TABLE economic_indicators (
                indicator_id text, frequency text,
                timestamp timestamp, value double,
                revision_number int,
                PRIMARY KEY ((indicator_id), timestamp, revision_number)
            ) WITH CLUSTERING ORDER BY (timestamp DESC, revision_number DESC)
        """)
        fund_stmt = self.session.prepare(
            "INSERT INTO fundamentals (symbol,period,revenue,net_income,eps,pe_ratio,"
            "book_value,dividend_yield,debt_to_equity,roe,roa,current_ratio,"
            "gross_margin,operating_margin,free_cash_flow,market_cap,balance_sheet,cash_flow) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        econ_stmt = self.session.prepare(
            "INSERT INTO economic_indicators (indicator_id,frequency,timestamp,value,revision_number) "
            "VALUES (?,?,?,?,?)"
        )
        for r in fundamentals:
            self.session.execute(fund_stmt, list(r.values()))
        for r in economic:
            self.session.execute(econ_stmt, list(r.values()))

    def single_query_fundamentals(self):
        rows = self.session.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals"
        )
        return len([r for r in rows if r.pe_ratio < 20 and r.revenue > 1e10])

    def single_query_economic(self):
        rows = self.session.execute(
            "SELECT indicator_id, timestamp, value, revision_number FROM economic_indicators LIMIT 100"
        )
        return len(list(rows))

    def multi_query_workload(self):
        total = 0
        rows = list(self.session.execute("SELECT symbol, eps, pe_ratio FROM fundamentals"))
        total += len(rows)
        rows = list(
            self.session.execute("SELECT symbol, period, revenue FROM fundamentals")
        )
        total += len(rows)
        rows = list(
            self.session.execute("SELECT indicator_id, value FROM economic_indicators")
        )
        total += len(rows)
        return total

    def teardown(self):
        self.cluster.shutdown()


# ---- ClickHouse -------------------------------------------------------------

class ClickHouseAdapter(DBAdapter):
    name = "ClickHouse"

    def setup(self, fundamentals, economic):
        import clickhouse_connect

        self.client = clickhouse_connect.get_client(host="localhost", port=8123, username="default", password="bench")
        self.client.command("DROP TABLE IF EXISTS bench.fundamentals")
        self.client.command("DROP TABLE IF EXISTS bench.economic_indicators")
        self.client.command("CREATE DATABASE IF NOT EXISTS bench")
        self.client.command("""
            CREATE TABLE bench.fundamentals (
                symbol String, period String, revenue Float64,
                net_income Float64, eps Float64, pe_ratio Float64,
                book_value Float64, dividend_yield Float64,
                debt_to_equity Float64, roe Float64, roa Float64,
                current_ratio Float64, gross_margin Float64,
                operating_margin Float64, free_cash_flow Float64,
                market_cap Float64, balance_sheet String, cash_flow String
            ) ENGINE = MergeTree() ORDER BY (symbol, period)
        """)
        self.client.command("""
            CREATE TABLE bench.economic_indicators (
                indicator_id String, frequency String,
                timestamp DateTime, value Float64,
                revision_number Int32
            ) ENGINE = MergeTree() ORDER BY (indicator_id, timestamp, revision_number)
        """)
        cols_f = list(fundamentals[0].keys())
        data_f = [list(r.values()) for r in fundamentals]
        self.client.insert("bench.fundamentals", data_f, column_names=cols_f)
        cols_e = list(economic[0].keys())
        data_e = [list(r.values()) for r in economic]
        self.client.insert("bench.economic_indicators", data_e, column_names=cols_e)

    def single_query_fundamentals(self):
        result = self.client.query(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM bench.fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        return result.row_count

    def single_query_economic(self):
        result = self.client.query("""
            SELECT indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM bench.economic_indicators
            ) WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
        """)
        return result.row_count

    def multi_query_workload(self):
        total = 0
        r = self.client.query(
            "SELECT symbol, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe FROM bench.fundamentals GROUP BY symbol ORDER BY avg_pe"
        )
        total += r.row_count
        r = self.client.query(
            "SELECT symbol, period, revenue, lagInFrame(revenue, 4) OVER (PARTITION BY symbol ORDER BY period) AS rev_yoy FROM bench.fundamentals"
        )
        total += r.row_count
        r = self.client.query(
            "SELECT indicator_id, AVG(value) AS avg_val FROM bench.economic_indicators GROUP BY indicator_id"
        )
        total += r.row_count
        return total

    def teardown(self):
        self.client.close()


# ---- TimescaleDB (PostgreSQL extension) -------------------------------------

class TimescaleDBAdapter(DBAdapter):
    name = "TimescaleDB"

    def setup(self, fundamentals, economic):
        import psycopg2

        self.con = psycopg2.connect(
            host="localhost", port=5433, user="bench", password="bench", dbname="bench"
        )
        self.con.autocommit = True
        cur = self.con.cursor()
        cur.execute("DROP TABLE IF EXISTS fundamentals")
        cur.execute("DROP TABLE IF EXISTS economic_indicators")
        cur.execute("""
            CREATE TABLE fundamentals (
                symbol TEXT, period TEXT, revenue DOUBLE PRECISION,
                net_income DOUBLE PRECISION, eps DOUBLE PRECISION, pe_ratio DOUBLE PRECISION,
                book_value DOUBLE PRECISION, dividend_yield DOUBLE PRECISION,
                debt_to_equity DOUBLE PRECISION, roe DOUBLE PRECISION, roa DOUBLE PRECISION,
                current_ratio DOUBLE PRECISION, gross_margin DOUBLE PRECISION,
                operating_margin DOUBLE PRECISION, free_cash_flow DOUBLE PRECISION,
                market_cap DOUBLE PRECISION, balance_sheet TEXT, cash_flow TEXT,
                PRIMARY KEY (symbol, period)
            )
        """)
        cur.execute("""
            CREATE TABLE economic_indicators (
                indicator_id TEXT, frequency TEXT,
                timestamp TIMESTAMP NOT NULL, value DOUBLE PRECISION,
                revision_number INTEGER,
                PRIMARY KEY (indicator_id, frequency, timestamp, revision_number)
            )
        """)
        for r in fundamentals:
            cur.execute(
                "INSERT INTO fundamentals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(r.values()),
            )
        for r in economic:
            cur.execute(
                "INSERT INTO economic_indicators VALUES (%s,%s,%s,%s,%s)",
                tuple(r.values()),
            )

    def single_query_fundamentals(self):
        cur = self.con.cursor()
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        return len(cur.fetchall())

    def single_query_economic(self):
        cur = self.con.cursor()
        cur.execute("""
            SELECT indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM economic_indicators
            ) sub WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
        """)
        return len(cur.fetchall())

    def multi_query_workload(self):
        cur = self.con.cursor()
        total = 0
        cur.execute(
            "SELECT symbol, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe FROM fundamentals GROUP BY symbol ORDER BY avg_pe"
        )
        total += len(cur.fetchall())
        cur.execute(
            "SELECT symbol, period, revenue, LAG(revenue,4) OVER (PARTITION BY symbol ORDER BY period) AS rev_yoy FROM fundamentals"
        )
        total += len(cur.fetchall())
        cur.execute(
            "SELECT indicator_id, AVG(value) AS avg_val FROM economic_indicators GROUP BY indicator_id"
        )
        total += len(cur.fetchall())
        return total

    def teardown(self):
        self.con.close()


# ---- Redis ------------------------------------------------------------------

class RedisAdapter(DBAdapter):
    name = "Redis"

    def setup(self, fundamentals, economic):
        import redis

        self.r = redis.Redis(host="localhost", port=6379, decode_responses=True)
        self.r.flushdb()
        pipe = self.r.pipeline()
        for row in fundamentals:
            key = f"fund:{row['symbol']}:{row['period']}"
            pipe.hset(key, mapping={k: str(v) for k, v in row.items()})
        pipe.execute()
        pipe = self.r.pipeline()
        for row in economic:
            key = f"econ:{row['indicator_id']}:{row['timestamp']}:{row['revision_number']}"
            pipe.hset(key, mapping={k: str(v) for k, v in row.items()})
        pipe.execute()
        self._fundamentals = fundamentals
        self._economic = economic

    def single_query_fundamentals(self):
        results = []
        for key in self.r.scan_iter("fund:*"):
            data = self.r.hgetall(key)
            if float(data.get("pe_ratio", 999)) < 20 and float(data.get("revenue", 0)) > 1e10:
                results.append(data)
        return len(results)

    def single_query_economic(self):
        results = []
        for key in self.r.scan_iter("econ:*"):
            data = self.r.hgetall(key)
            results.append(data)
            if len(results) >= 100:
                break
        return len(results)

    def multi_query_workload(self):
        total = 0
        all_fund = []
        for key in self.r.scan_iter("fund:*"):
            all_fund.append(self.r.hgetall(key))
        total += len(all_fund)
        all_econ = []
        for key in self.r.scan_iter("econ:*"):
            all_econ.append(self.r.hgetall(key))
        total += len(all_econ)
        total += len(all_fund)
        return total

    def teardown(self):
        self.r.close()


# ---- RavenDB ----------------------------------------------------------------

class RavenDBAdapter(DBAdapter):
    name = "RavenDB"

    def setup(self, fundamentals, economic):
        import requests

        self.base_url = "http://localhost:8080"
        self.db_name = "bench"
        # Create database
        try:
            requests.put(
                f"{self.base_url}/admin/databases",
                json={
                    "DatabaseRecord": {"DatabaseName": self.db_name},
                    "DatabaseTopology": {"Members": ["A"]},
                },
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
        except Exception:
            pass
        # Bulk insert fundamentals
        for r in fundamentals:
            doc_id = f"fundamentals/{r['symbol']}-{r['period']}"
            requests.put(
                f"{self.base_url}/databases/{self.db_name}/docs?id={doc_id}",
                json=r,
                headers={"Content-Type": "application/json"},
                timeout=5,
            )
        for r in economic:
            doc_id = f"economic/{r['indicator_id']}-{r['timestamp']}-{r['revision_number']}"
            requests.put(
                f"{self.base_url}/databases/{self.db_name}/docs?id={doc_id}",
                json=r,
                headers={"Content-Type": "application/json"},
                timeout=5,
            )

    def single_query_fundamentals(self):
        import requests

        resp = requests.get(
            f"{self.base_url}/databases/{self.db_name}/docs",
            params={"startsWith": "fundamentals/", "pageSize": 1024},
            timeout=10,
        )
        results = resp.json().get("Results", [])
        return len([r for r in results if r.get("pe_ratio", 999) < 20 and r.get("revenue", 0) > 1e10])

    def single_query_economic(self):
        import requests

        resp = requests.get(
            f"{self.base_url}/databases/{self.db_name}/docs",
            params={"startsWith": "economic/", "pageSize": 100},
            timeout=10,
        )
        return len(resp.json().get("Results", []))

    def multi_query_workload(self):
        import requests

        total = 0
        resp = requests.get(
            f"{self.base_url}/databases/{self.db_name}/docs",
            params={"startsWith": "fundamentals/", "pageSize": 1024},
            timeout=10,
        )
        total += len(resp.json().get("Results", []))
        resp = requests.get(
            f"{self.base_url}/databases/{self.db_name}/docs",
            params={"startsWith": "economic/", "pageSize": 1024},
            timeout=10,
        )
        total += len(resp.json().get("Results", []))
        # Re-fetch fundamentals for a third read pass (mimics multi-query pattern)
        resp = requests.get(
            f"{self.base_url}/databases/{self.db_name}/docs",
            params={"startsWith": "fundamentals/", "pageSize": 1024},
            timeout=10,
        )
        total += len(resp.json().get("Results", []))
        return total


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_ADAPTERS: list[type[DBAdapter]] = [
    DuckDBAdapter,
    SQLiteAdapter,
    PostgreSQLAdapter,
    MySQLAdapter,
    MariaDBAdapter,
    MongoDBAdapter,
    FerretDBAdapter,
    CassandraAdapter,
    ScyllaDBAdapter,
    ClickHouseAdapter,
    TimescaleDBAdapter,
    RedisAdapter,
    RavenDBAdapter,
]

ADAPTER_MAP: dict[str, type[DBAdapter]] = {a.name.lower(): a for a in ALL_ADAPTERS}


def run_benchmark(
    adapter_cls: type[DBAdapter],
    fundamentals: list[dict],
    economic: list[dict],
    iterations: int = 3,
) -> list[BenchResult]:
    results: list[BenchResult] = []
    adapter = adapter_cls()
    db = adapter.name

    # Setup / data load
    try:
        with _timer() as t:
            adapter.setup(fundamentals, economic)
        results.append(BenchResult(db, "data_load", t[0], len(fundamentals) + len(economic)))
    except Exception as exc:
        results.append(BenchResult(db, "data_load", 0, error=str(exc)))
        return results

    # Single-query: fundamentals screening
    for i in range(iterations):
        try:
            with _timer() as t:
                n = adapter.single_query_fundamentals()
            results.append(BenchResult(db, "single_query_fundamentals", t[0], n))
        except Exception as exc:
            results.append(BenchResult(db, "single_query_fundamentals", 0, error=str(exc)))

    # Single-query: economic latest values
    for i in range(iterations):
        try:
            with _timer() as t:
                n = adapter.single_query_economic()
            results.append(BenchResult(db, "single_query_economic", t[0], n))
        except Exception as exc:
            results.append(BenchResult(db, "single_query_economic", 0, error=str(exc)))

    # Multi-query workload
    for i in range(iterations):
        try:
            with _timer() as t:
                n = adapter.multi_query_workload()
            results.append(BenchResult(db, "multi_query_workload", t[0], n))
        except Exception as exc:
            results.append(BenchResult(db, "multi_query_workload", 0, error=str(exc)))

    try:
        adapter.teardown()
    except Exception:
        pass

    return results


def generate_summary(all_results: list[BenchResult], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Group results
    ops = ["data_load", "single_query_fundamentals", "single_query_economic", "multi_query_workload"]
    op_labels = {
        "data_load": "Data Load",
        "single_query_fundamentals": "Single Query: Fundamentals Screening",
        "single_query_economic": "Single Query: Economic Latest Values",
        "multi_query_workload": "Multi-Query Workload",
    }

    db_names: list[str] = []
    seen: set[str] = set()
    for r in all_results:
        if r.db_name not in seen:
            seen.add(r.db_name)
            db_names.append(r.db_name)

    lines: list[str] = []
    lines.append("# Fundamentals Database Benchmark Results\n")
    lines.append("## Configuration\n")
    lines.append(f"- **Databases tested**: {len(db_names)}")
    lines.append(f"- **Databases**: {', '.join(db_names)}")
    lines.append(f"- **Fundamentals records**: 30 symbols × 28 periods = 840 rows")
    lines.append(f"- **Economic records**: ~1,680 rows (10 indicators × 84 months × ~2 revisions)\n")

    for op in ops:
        lines.append(f"## {op_labels[op]}\n")
        lines.append("| Database | Avg (ms) | Min (ms) | Max (ms) | Rows | Error |")
        lines.append("| --- | ---: | ---: | ---: | ---: | --- |")

        for db in db_names:
            op_results = [r for r in all_results if r.db_name == db and r.operation == op]
            if not op_results:
                lines.append(f"| {db} | — | — | — | — | skipped |")
                continue
            errors = [r for r in op_results if r.error]
            if errors:
                lines.append(f"| {db} | — | — | — | — | {errors[0].error[:80]} |")
                continue
            times = [r.elapsed_ms for r in op_results]
            avg = statistics.mean(times)
            mn = min(times)
            mx = max(times)
            row_count = op_results[0].row_count
            lines.append(f"| {db} | {avg:.2f} | {mn:.2f} | {mx:.2f} | {row_count} | — |")

        lines.append("")

    summary = "\n".join(lines)
    summary_path = output_dir / "summary.md"
    summary_path.write_text(summary)
    print(summary)

    # Also dump raw JSON
    raw = [
        {
            "db": r.db_name,
            "op": r.operation,
            "ms": round(r.elapsed_ms, 4),
            "rows": r.row_count,
            "error": r.error,
        }
        for r in all_results
    ]
    (output_dir / "raw_results.json").write_text(json.dumps(raw, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Fundamentals database benchmarks")
    parser.add_argument(
        "--databases",
        nargs="*",
        default=None,
        help="Space-separated database names to benchmark (default: all)",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3,
        help="Number of iterations per query type (default: 3)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="benchmarks/results/fundamentals",
        help="Output directory for results",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Fundamentals Database Benchmark Suite")
    print("=" * 60)

    fundamentals = generate_fundamentals()
    economic = generate_economic_data()
    print(f"Generated {len(fundamentals)} fundamental records, {len(economic)} economic records\n")

    if args.databases:
        adapters = []
        for name in args.databases:
            cls = ADAPTER_MAP.get(name.lower())
            if cls is None:
                print(f"WARNING: Unknown database '{name}', skipping. Available: {list(ADAPTER_MAP.keys())}")
            else:
                adapters.append(cls)
    else:
        adapters = list(ALL_ADAPTERS)

    all_results: list[BenchResult] = []

    for adapter_cls in adapters:
        db = adapter_cls.name
        print(f"\n{'─' * 40}")
        print(f"Benchmarking: {db}")
        print(f"{'─' * 40}")
        results = run_benchmark(adapter_cls, fundamentals, economic, iterations=args.iterations)
        all_results.extend(results)
        for r in results:
            status = f"{r.elapsed_ms:.2f}ms" if not r.error else f"ERROR: {r.error[:60]}"
            print(f"  {r.operation}: {status}")

    print(f"\n{'=' * 60}")
    print("Generating summary …")
    generate_summary(all_results, Path(args.output_dir))
    print(f"Results written to {args.output_dir}/")


if __name__ == "__main__":
    main()
