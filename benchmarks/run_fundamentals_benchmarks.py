"""
Fundamentals Database Benchmark Suite

Benchmarks 13 databases for stock fundamentals and economic data workloads:
  - DuckDB, SQLite (embedded)
  - PostgreSQL, SQL Server (mssql-python), SQL Server (pyodbc), MySQL, TimescaleDB (relational / Docker)
  - MongoDB (document / Docker)
  - Cassandra, ScyllaDB (wide-column / Docker)
  - Redis (key-value / Docker)
  - ClickHouse (columnar / Docker)
  - RavenDB (document / Docker)

Generates synthetic stock fundamentals (~100 K rows) and economic indicator
data (~100 K rows), then runs:
  1. Simple Query workload  (targeted single-table queries)
  2. Complex Query workload (double groupby + full table scan)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import random
import statistics
import time
import traceback
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

PERIODS = [f"{y}-Q{q}" for y in range(1975, 2026) for q in range(1, 5)]

ECON_INDICATORS = [
    "GDP_US", "CPI_US", "UNRATE_US", "GDP_EU", "CPI_EU", "FEDFUNDS",
    "T10Y2Y", "INDPRO", "PAYEMS", "PCE_US",
]

ECON_FREQUENCIES = ["monthly", "quarterly"]


def _rand_float(lo: float, hi: float) -> float:
    return round(random.uniform(lo, hi), 4)


def generate_fundamentals(n_symbols: int = 500, n_periods: int = 200) -> list[dict]:
    """Return a list of fundamental records (symbol x period). ~100 K rows by default."""
    random.seed(42)
    real = SYMBOLS[:min(n_symbols, len(SYMBOLS))]
    synth = [f"SYM{i:04d}" for i in range(1, n_symbols - len(real) + 1)]
    symbols = real + synth
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


def generate_economic_data(n_indicators: int = 250, n_months: int = 200) -> list[dict]:
    """Return a list of economic indicator records."""
    random.seed(43)
    real = ECON_INDICATORS[:min(n_indicators, len(ECON_INDICATORS))]
    synth = [f"ECON{i:04d}" for i in range(1, n_indicators - len(real) + 1)]
    indicators = real + synth
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

    def simple_query_fundamentals(self) -> int:
        raise NotImplementedError

    def simple_query_economic(self) -> int:
        raise NotImplementedError

    def complex_query_workload(self) -> int:
        raise NotImplementedError

    def teardown(self) -> None:
        pass


# ---- DuckDB ----------------------------------------------------------------

class DuckDBAdapter(DBAdapter):
    name = "DuckDB"

    def setup(self, fundamentals, economic):
        import duckdb
        import json
        import os
        import tempfile

        self.con = duckdb.connect(":memory:", config={"threads": 2})
        self.con.execute("""
            CREATE TABLE fundamentals (
                symbol VARCHAR, period VARCHAR, revenue DOUBLE,
                net_income DOUBLE, eps DOUBLE, pe_ratio DOUBLE,
                book_value DOUBLE, dividend_yield DOUBLE,
                debt_to_equity DOUBLE, roe DOUBLE, roa DOUBLE,
                current_ratio DOUBLE, gross_margin DOUBLE,
                operating_margin DOUBLE, free_cash_flow DOUBLE,
                market_cap DOUBLE, balance_sheet VARCHAR, cash_flow VARCHAR,
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
        # Use read_json for fast bulk loading
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            json.dump(fundamentals, fp)
            fund_tmp = fp.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fp:
            json.dump(economic, fp)
            econ_tmp = fp.name
        try:
            self.con.execute(f"INSERT INTO fundamentals SELECT * FROM read_json('{fund_tmp}')")
            self.con.execute(f"INSERT INTO economic_indicators SELECT * FROM read_json('{econ_tmp}')")
        finally:
            os.unlink(fund_tmp)
            os.unlink(econ_tmp)

    def simple_query_fundamentals(self):
        return len(
            self.con.execute(
                "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
            ).fetchall()
        )

    def simple_query_economic(self):
        return len(
            self.con.execute("""
                SELECT indicator_id, timestamp, value FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                    ) AS rn FROM economic_indicators
                ) WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
            """).fetchall()
        )

    def complex_query_workload(self):
        total = 0
        # Double groupby: aggregate by symbol and year extracted from period
        total += len(
            self.con.execute("""
                SELECT symbol, SUBSTR(period, 1, 4) AS yr,
                       AVG(revenue) AS avg_rev, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe
                FROM fundamentals
                GROUP BY symbol, SUBSTR(period, 1, 4)
                ORDER BY symbol, yr
            """).fetchall()
        )
        # Full table scan: unindexed multi-column filter
        total += len(
            self.con.execute(
                "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE gross_margin > 0.3 AND roe > 0.05"
            ).fetchall()
        )
        # Double groupby on economic data
        total += len(
            self.con.execute("""
                SELECT indicator_id, frequency,
                       AVG(value) AS avg_val, COUNT(*) AS cnt,
                       MIN(value) AS min_val, MAX(value) AS max_val
                FROM economic_indicators
                GROUP BY indicator_id, frequency
            """).fetchall()
        )
        return total

    def teardown(self):
        self.con.close()


# ---- SQLite -----------------------------------------------------------------

class SQLiteAdapter(DBAdapter):
    name = "SQLite"

    def setup(self, fundamentals, economic):
        import sqlite3

        # check_same_thread=False is required to allow 2 threads to share this
        # in-memory connection for concurrent inserts into different tables.
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=OFF")
        self.con.execute("PRAGMA synchronous=OFF")
        self.con.execute("PRAGMA cache_size=-128000")
        self.con.execute("PRAGMA temp_store=MEMORY")
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
        # Use 2 threads to insert into both tables concurrently. SQLite
        # in-memory has only one connection so the two inserts share it;
        # writes will serialize at the SQLite lock but the threads still run
        # in parallel from Python's perspective.
        def _ins_fund():
            self.con.executemany(
                "INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [tuple(r.values()) for r in fundamentals],
            )

        def _ins_econ():
            self.con.executemany(
                "INSERT INTO economic_indicators VALUES (?,?,?,?,?)",
                [tuple(r.values()) for r in economic],
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_fund), ex.submit(_ins_econ)]
            for f in fs:
                f.result()
        self.con.commit()
        self.con.execute("CREATE INDEX idx_fund_pe_rev ON fundamentals (pe_ratio, revenue)")
        self.con.execute("CREATE INDEX idx_fund_gm_roe ON fundamentals (gross_margin, roe)")
        self.con.execute("CREATE INDEX idx_econ_rev ON economic_indicators (indicator_id, timestamp, revision_number DESC)")

    def simple_query_fundamentals(self):
        return len(
            self.con.execute(
                "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
            ).fetchall()
        )

    def simple_query_economic(self):
        return len(
            self.con.execute("""
                SELECT indicator_id, timestamp, value FROM (
                    SELECT *, ROW_NUMBER() OVER (
                        PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                    ) AS rn FROM economic_indicators
                ) WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
            """).fetchall()
        )

    def complex_query_workload(self):
        total = 0
        # Double groupby: aggregate by symbol and year extracted from period
        total += len(
            self.con.execute("""
                SELECT symbol, SUBSTR(period, 1, 4) AS yr,
                       AVG(revenue) AS avg_rev, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe
                FROM fundamentals
                GROUP BY symbol, SUBSTR(period, 1, 4)
                ORDER BY symbol, yr
            """).fetchall()
        )
        # Full table scan: unindexed multi-column filter
        total += len(
            self.con.execute(
                "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE gross_margin > 0.3 AND roe > 0.05"
            ).fetchall()
        )
        # Double groupby on economic data
        total += len(
            self.con.execute("""
                SELECT indicator_id, frequency,
                       AVG(value) AS avg_val, COUNT(*) AS cnt,
                       MIN(value) AS min_val, MAX(value) AS max_val
                FROM economic_indicators
                GROUP BY indicator_id, frequency
            """).fetchall()
        )
        return total

    def teardown(self):
        self.con.close()


# ---- PostgreSQL -------------------------------------------------------------

class PostgreSQLAdapter(DBAdapter):
    name = "PostgreSQL"

    def setup(self, fundamentals, economic):
        import psycopg2
        from psycopg2.extras import execute_values

        self.con = psycopg2.connect(
            host="localhost", port=5432, user="bench", password="bench", dbname="bench"
        )
        self.con.autocommit = True
        cur = self.con.cursor()
        cur.execute("SET work_mem = '256MB'")
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
        # Use 2 threads for parallel bulk inserts (psycopg2 connections are thread-safe
        # when each thread owns its own connection)
        _pg_params = dict(host="localhost", port=5432, user="bench", password="bench", dbname="bench")

        def _ins_fund(rows):
            import psycopg2
            from psycopg2.extras import execute_values
            c = psycopg2.connect(**_pg_params)
            c.autocommit = True
            execute_values(c.cursor(), "INSERT INTO fundamentals VALUES %s",
                           [tuple(r.values()) for r in rows], page_size=1000)
            c.close()

        def _ins_econ(rows):
            import psycopg2
            from psycopg2.extras import execute_values
            c = psycopg2.connect(**_pg_params)
            c.autocommit = True
            execute_values(c.cursor(), "INSERT INTO economic_indicators VALUES %s",
                           [tuple(r.values()) for r in rows], page_size=1000)
            c.close()

        mid_f = len(fundamentals) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_fund, fundamentals[:mid_f]), ex.submit(_ins_fund, fundamentals[mid_f:])]
            for f in fs:
                f.result()

        mid_e = len(economic) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_econ, economic[:mid_e]), ex.submit(_ins_econ, economic[mid_e:])]
            for f in fs:
                f.result()

        cur = self.con.cursor()
        cur.execute("CREATE INDEX idx_fund_pe_rev ON fundamentals (pe_ratio, revenue)")
        cur.execute("CREATE INDEX idx_fund_gm_roe ON fundamentals (gross_margin, roe)")
        cur.execute("CREATE INDEX idx_econ_rev ON economic_indicators (indicator_id, timestamp, revision_number DESC)")
        cur.execute("ANALYZE fundamentals")
        cur.execute("ANALYZE economic_indicators")

    def simple_query_fundamentals(self):
        cur = self.con.cursor()
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        return len(cur.fetchall())

    def simple_query_economic(self):
        cur = self.con.cursor()
        cur.execute("""
            SELECT indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM economic_indicators
            ) sub WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
        """)
        return len(cur.fetchall())

    def complex_query_workload(self):
        cur = self.con.cursor()
        total = 0
        # Double groupby: aggregate by symbol and year extracted from period
        cur.execute("""
            SELECT symbol, SUBSTR(period, 1, 4) AS yr,
                   AVG(revenue) AS avg_rev, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe
            FROM fundamentals
            GROUP BY symbol, SUBSTR(period, 1, 4)
            ORDER BY symbol, yr
        """)
        total += len(cur.fetchall())
        # Full table scan: unindexed multi-column filter
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE gross_margin > 0.3 AND roe > 0.05"
        )
        total += len(cur.fetchall())
        # Double groupby on economic data
        cur.execute("""
            SELECT indicator_id, frequency,
                   AVG(value) AS avg_val, COUNT(*) AS cnt,
                   MIN(value) AS min_val, MAX(value) AS max_val
            FROM economic_indicators
            GROUP BY indicator_id, frequency
        """)
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

        # Use 2 threads for parallel bulk inserts (mysql-connector connections are not
        # thread-safe; each thread must own its own connection)
        _mysql_params = dict(host="127.0.0.1", port=3306, user="bench", password="bench", database="bench")

        def _ins_fund(rows):
            import mysql.connector
            c = mysql.connector.connect(**_mysql_params)
            cur = c.cursor()
            cur.executemany(
                "INSERT INTO fundamentals VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                [tuple(r.values()) for r in rows],
            )
            c.commit()
            c.close()

        def _ins_econ(rows):
            import mysql.connector
            c = mysql.connector.connect(**_mysql_params)
            cur = c.cursor()
            cur.executemany(
                "INSERT INTO economic_indicators VALUES (%s,%s,%s,%s,%s)",
                [tuple(r.values()) for r in rows],
            )
            c.commit()
            c.close()

        mid_f = len(fundamentals) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_fund, fundamentals[:mid_f]), ex.submit(_ins_fund, fundamentals[mid_f:])]
            for f in fs:
                f.result()

        mid_e = len(economic) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_econ, economic[:mid_e]), ex.submit(_ins_econ, economic[mid_e:])]
            for f in fs:
                f.result()

        cur = self.con.cursor()
        cur.execute("CREATE INDEX idx_fund_pe_rev ON fundamentals (pe_ratio, revenue)")
        cur.execute("CREATE INDEX idx_fund_gm_roe ON fundamentals (gross_margin, roe)")
        cur.execute("CREATE INDEX idx_econ_rev ON economic_indicators (indicator_id, timestamp, revision_number DESC)")
        self.con.commit()

    def simple_query_fundamentals(self):
        cur = self.con.cursor()
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        return len(cur.fetchall())

    def simple_query_economic(self):
        cur = self.con.cursor()
        cur.execute("""
            SELECT indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM economic_indicators
            ) sub WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
        """)
        return len(cur.fetchall())

    def complex_query_workload(self):
        cur = self.con.cursor()
        total = 0
        # Double groupby: aggregate by symbol and year extracted from period
        cur.execute("""
            SELECT symbol, SUBSTR(period, 1, 4) AS yr,
                   AVG(revenue) AS avg_rev, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe
            FROM fundamentals
            GROUP BY symbol, SUBSTR(period, 1, 4)
            ORDER BY symbol, yr
        """)
        total += len(cur.fetchall())
        # Full table scan: unindexed multi-column filter
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE gross_margin > 0.3 AND roe > 0.05"
        )
        total += len(cur.fetchall())
        # Double groupby on economic data
        cur.execute("""
            SELECT indicator_id, frequency,
                   AVG(value) AS avg_val, COUNT(*) AS cnt,
                   MIN(value) AS min_val, MAX(value) AS max_val
            FROM economic_indicators
            GROUP BY indicator_id, frequency
        """)
        total += len(cur.fetchall())
        return total

    def teardown(self):
        self.con.close()


# ---- SQL Server (mssql-python) ----------------------------------------------

class MSSQLPythonAdapter(DBAdapter):
    name = "SQL Server (mssql-python)"

    def setup(self, fundamentals, economic):
        import mssql_python
        from datetime import datetime

        conn_str = (
            "SERVER=127.0.0.1,1433;"
            "DATABASE=bench;"
            "UID=sa;"
            "PWD=Bench!1234;"
            "TrustServerCertificate=yes;"
            "Encrypt=no;"
        )
        self.con = mssql_python.connect(conn_str)
        cur = self.con.cursor()
        cur.execute("DROP TABLE IF EXISTS fundamentals")
        cur.execute("DROP TABLE IF EXISTS economic_indicators")
        cur.execute("""
            CREATE TABLE fundamentals (
                symbol VARCHAR(20), period VARCHAR(20), revenue FLOAT,
                net_income FLOAT, eps FLOAT, pe_ratio FLOAT,
                book_value FLOAT, dividend_yield FLOAT,
                debt_to_equity FLOAT, roe FLOAT, roa FLOAT,
                current_ratio FLOAT, gross_margin FLOAT,
                operating_margin FLOAT, free_cash_flow FLOAT,
                market_cap FLOAT, balance_sheet VARCHAR(MAX), cash_flow VARCHAR(MAX),
                PRIMARY KEY (symbol, period)
            )
        """)
        cur.execute("""
            CREATE TABLE economic_indicators (
                indicator_id VARCHAR(30), frequency VARCHAR(20),
                timestamp DATETIME2, value FLOAT,
                revision_number INTEGER,
                PRIMARY KEY (indicator_id, frequency, timestamp, revision_number)
            )
        """)
        self.con.commit()
        cur.close()

        # Use 2 threads for parallel bulk inserts. mssql_python connections are
        # not safe to share across threads; each thread owns its own connection.
        def _ins_fund(rows):
            c = mssql_python.connect(conn_str)
            cur = c.cursor()
            cur.executemany(
                "INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [tuple(r.values()) for r in rows],
            )
            c.commit()
            c.close()

        def _ins_econ(rows):
            c = mssql_python.connect(conn_str)
            cur = c.cursor()
            cur.executemany(
                "INSERT INTO economic_indicators VALUES (?,?,?,?,?)",
                [
                    (r["indicator_id"], r["frequency"],
                     datetime.fromisoformat(r["timestamp"]),
                     r["value"], r["revision_number"])
                    for r in rows
                ],
            )
            c.commit()
            c.close()

        mid_f = len(fundamentals) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_fund, fundamentals[:mid_f]), ex.submit(_ins_fund, fundamentals[mid_f:])]
            for f in fs:
                f.result()

        mid_e = len(economic) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_econ, economic[:mid_e]), ex.submit(_ins_econ, economic[mid_e:])]
            for f in fs:
                f.result()

        cur = self.con.cursor()
        cur.execute("CREATE INDEX idx_fund_pe_rev ON fundamentals (pe_ratio, revenue)")
        cur.execute("CREATE INDEX idx_fund_gm_roe ON fundamentals (gross_margin, roe)")
        cur.execute("CREATE INDEX idx_econ_rev ON economic_indicators (indicator_id, timestamp, revision_number DESC)")
        cur.execute("UPDATE STATISTICS fundamentals")
        cur.execute("UPDATE STATISTICS economic_indicators")
        self.con.commit()
        cur.close()

    def simple_query_fundamentals(self):
        cur = self.con.cursor()
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals"
            " WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        n = len(cur.fetchall())
        cur.close()
        return n

    def simple_query_economic(self):
        cur = self.con.cursor()
        cur.execute("""
            SELECT TOP 100 indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM economic_indicators
            ) sub WHERE rn = 1 ORDER BY timestamp DESC
        """)
        n = len(cur.fetchall())
        cur.close()
        return n

    def complex_query_workload(self):
        total = 0
        cur = self.con.cursor()
        cur.execute("""
            SELECT symbol, SUBSTRING(period, 1, 4) AS yr,
                   AVG(revenue) AS avg_rev, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe
            FROM fundamentals
            GROUP BY symbol, SUBSTRING(period, 1, 4)
            ORDER BY symbol, yr
        """)
        total += len(cur.fetchall())
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals"
            " WHERE gross_margin > 0.3 AND roe > 0.05"
        )
        total += len(cur.fetchall())
        cur.execute("""
            SELECT indicator_id, frequency,
                   AVG(value) AS avg_val, COUNT(*) AS cnt,
                   MIN(value) AS min_val, MAX(value) AS max_val
            FROM economic_indicators
            GROUP BY indicator_id, frequency
        """)
        total += len(cur.fetchall())
        cur.close()
        return total

    def teardown(self):
        self.con.close()


# ---- SQL Server (pyodbc) ----------------------------------------------------

class PyODBCMSSQLAdapter(DBAdapter):
    name = "SQL Server (pyodbc)"

    def setup(self, fundamentals, economic):
        import pyodbc
        from datetime import datetime

        conn_str = (
            "DRIVER={ODBC Driver 18 for SQL Server};"
            "SERVER=127.0.0.1,1433;"
            "DATABASE=bench;"
            "UID=sa;"
            "PWD=Bench!1234;"
            "TrustServerCertificate=yes;"
        )
        self._conn_str = conn_str
        self.con = pyodbc.connect(conn_str)
        cur = self.con.cursor()
        cur.execute("SET NOCOUNT ON")
        cur.execute("DROP TABLE IF EXISTS fundamentals")
        cur.execute("DROP TABLE IF EXISTS economic_indicators")
        cur.execute("""
            CREATE TABLE fundamentals (
                symbol VARCHAR(20), period VARCHAR(20), revenue FLOAT,
                net_income FLOAT, eps FLOAT, pe_ratio FLOAT,
                book_value FLOAT, dividend_yield FLOAT,
                debt_to_equity FLOAT, roe FLOAT, roa FLOAT,
                current_ratio FLOAT, gross_margin FLOAT,
                operating_margin FLOAT, free_cash_flow FLOAT,
                market_cap FLOAT, balance_sheet VARCHAR(MAX), cash_flow VARCHAR(MAX),
                PRIMARY KEY (symbol, period)
            )
        """)
        cur.execute("""
            CREATE TABLE economic_indicators (
                indicator_id VARCHAR(30), frequency VARCHAR(20),
                timestamp DATETIME2, value FLOAT,
                revision_number INTEGER,
                PRIMARY KEY (indicator_id, frequency, timestamp, revision_number)
            )
        """)
        self.con.commit()

        # Use 2 threads with fast_executemany=True for parallel bulk inserts;
        # pyodbc connections must not be shared across threads.
        def _ins_fund(rows):
            import pyodbc
            c = pyodbc.connect(conn_str)
            cur = c.cursor()
            cur.fast_executemany = True
            cur.executemany(
                "INSERT INTO fundamentals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [tuple(r.values()) for r in rows],
            )
            c.commit()
            c.close()

        def _ins_econ(rows):
            import pyodbc
            from datetime import datetime
            c = pyodbc.connect(conn_str)
            cur = c.cursor()
            cur.fast_executemany = True
            cur.executemany(
                "INSERT INTO economic_indicators VALUES (?,?,?,?,?)",
                [
                    (r["indicator_id"], r["frequency"],
                     datetime.fromisoformat(r["timestamp"]),
                     r["value"], r["revision_number"])
                    for r in rows
                ],
            )
            c.commit()
            c.close()

        mid_f = len(fundamentals) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_fund, fundamentals[:mid_f]), ex.submit(_ins_fund, fundamentals[mid_f:])]
            for f in fs:
                f.result()

        mid_e = len(economic) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_econ, economic[:mid_e]), ex.submit(_ins_econ, economic[mid_e:])]
            for f in fs:
                f.result()

        cur = self.con.cursor()
        cur.execute("CREATE INDEX idx_fund_pe_rev ON fundamentals (pe_ratio, revenue)")
        cur.execute("CREATE INDEX idx_fund_gm_roe ON fundamentals (gross_margin, roe)")
        cur.execute("CREATE INDEX idx_econ_rev ON economic_indicators (indicator_id, timestamp, revision_number DESC)")
        cur.execute("UPDATE STATISTICS fundamentals")
        cur.execute("UPDATE STATISTICS economic_indicators")
        self.con.commit()

    def simple_query_fundamentals(self):
        cur = self.con.cursor()
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals"
            " WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        return len(cur.fetchall())

    def simple_query_economic(self):
        cur = self.con.cursor()
        cur.execute("""
            SELECT TOP 100 indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM economic_indicators
            ) sub WHERE rn = 1 ORDER BY timestamp DESC
        """)
        return len(cur.fetchall())

    def complex_query_workload(self):
        cur = self.con.cursor()
        total = 0
        # Double groupby: aggregate by symbol and year extracted from period
        cur.execute("""
            SELECT symbol, SUBSTRING(period, 1, 4) AS yr,
                   AVG(revenue) AS avg_rev, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe
            FROM fundamentals
            GROUP BY symbol, SUBSTRING(period, 1, 4)
            ORDER BY symbol, yr
        """)
        total += len(cur.fetchall())
        # Full table scan: unindexed multi-column filter
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals"
            " WHERE gross_margin > 0.3 AND roe > 0.05"
        )
        total += len(cur.fetchall())
        # Double groupby on economic data
        cur.execute("""
            SELECT indicator_id, frequency,
                   AVG(value) AS avg_val, COUNT(*) AS cnt,
                   MIN(value) AS min_val, MAX(value) AS max_val
            FROM economic_indicators
            GROUP BY indicator_id, frequency
        """)
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

        # MongoClient is thread-safe; use 2 threads for parallel bulk inserts
        def _ins_fund(rows):
            self.db["fundamentals"].insert_many([dict(r) for r in rows])

        def _ins_econ(rows):
            self.db["economic_indicators"].insert_many([dict(r) for r in rows])

        mid_f = len(fundamentals) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_fund, fundamentals[:mid_f]), ex.submit(_ins_fund, fundamentals[mid_f:])]
            for f in fs:
                f.result()

        mid_e = len(economic) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_econ, economic[:mid_e]), ex.submit(_ins_econ, economic[mid_e:])]
            for f in fs:
                f.result()

        self.db["fundamentals"].create_index([("symbol", 1), ("period", 1)], unique=True)
        self.db["fundamentals"].create_index([("pe_ratio", 1), ("revenue", 1)])
        self.db["fundamentals"].create_index([("gross_margin", 1), ("roe", 1)])
        self.db["economic_indicators"].create_index(
            [("indicator_id", 1), ("timestamp", 1), ("revision_number", -1)]
        )

    def simple_query_fundamentals(self):
        return len(
            list(
                self.db["fundamentals"].find(
                    {"pe_ratio": {"$lt": 20}, "revenue": {"$gt": 1e10}},
                    {"symbol": 1, "period": 1, "revenue": 1, "eps": 1, "pe_ratio": 1},
                ).sort("pe_ratio", 1)
            )
        )

    def simple_query_economic(self):
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

    def complex_query_workload(self):
        total = 0
        # Double groupby: aggregate by symbol and year extracted from period
        pipeline1 = [
            {"$addFields": {"yr": {"$substr": ["$period", 0, 4]}}},
            {
                "$group": {
                    "_id": {"symbol": "$symbol", "yr": "$yr"},
                    "avg_rev": {"$avg": "$revenue"},
                    "avg_eps": {"$avg": "$eps"},
                    "avg_pe": {"$avg": "$pe_ratio"},
                }
            },
            {"$sort": {"_id.symbol": 1, "_id.yr": 1}},
        ]
        total += len(list(self.db["fundamentals"].aggregate(pipeline1)))
        # Full table scan: unindexed multi-column filter
        total += len(
            list(
                self.db["fundamentals"].find(
                    {"gross_margin": {"$gt": 0.3}, "roe": {"$gt": 0.05}},
                    {"symbol": 1, "period": 1, "revenue": 1, "eps": 1, "pe_ratio": 1},
                )
            )
        )
        # Double groupby on economic data
        pipeline3 = [
            {
                "$group": {
                    "_id": {"indicator_id": "$indicator_id", "frequency": "$frequency"},
                    "avg_val": {"$avg": "$value"},
                    "cnt": {"$sum": 1},
                    "min_val": {"$min": "$value"},
                    "max_val": {"$max": "$value"},
                }
            }
        ]
        total += len(list(self.db["economic_indicators"].aggregate(pipeline3)))
        return total

    def teardown(self):
        self.client.close()


# ---- Cassandra --------------------------------------------------------------

class CassandraAdapter(DBAdapter):
    name = "Cassandra"

    def setup(self, fundamentals, economic):
        from cassandra.cluster import Cluster
        from cassandra.concurrent import execute_concurrent_with_args
        from datetime import datetime

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
        fund_params = [list(r.values()) for r in fundamentals]
        econ_params = [
            [r["indicator_id"], r["frequency"], datetime.fromisoformat(r["timestamp"]),
             r["value"], r["revision_number"]]
            for r in economic
        ]
        execute_concurrent_with_args(self.session, fund_stmt, fund_params, concurrency=200)
        execute_concurrent_with_args(self.session, econ_stmt, econ_params, concurrency=200)

    def simple_query_fundamentals(self):
        rows = self.session.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals "
            "WHERE pe_ratio < 20 AND revenue > 1e10 ALLOW FILTERING"
        )
        return len(list(rows))

    def simple_query_economic(self):
        rows = self.session.execute(
            "SELECT indicator_id, timestamp, value, revision_number FROM economic_indicators LIMIT 100"
        )
        return len(list(rows))

    def complex_query_workload(self):
        from cassandra.concurrent import execute_concurrent
        from cassandra.query import SimpleStatement

        total = 0
        # Execute all 3 queries concurrently using execute_concurrent()
        statements = [
            (SimpleStatement("SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals"), ()),
            (SimpleStatement(
                "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals "
                "WHERE gross_margin > 0.3 AND roe > 0.05 ALLOW FILTERING"
            ), ()),
            (SimpleStatement("SELECT indicator_id, frequency, value FROM economic_indicators"), ()),
        ]
        results = execute_concurrent(self.session, statements, concurrency=3, raise_on_first_error=True)
        # Collect results
        rows = list(results[0][1])
        result = list(results[1][1])
        econ_rows = list(results[2][1])
        # Full table scan + Python-side double groupby (symbol, year)
        groups_sy: dict = {}
        for r in rows:
            key = (r.symbol, r.period[:4])
            groups_sy[key] = groups_sy.get(key, 0) + 1
        total += len(groups_sy)
        total += len(result)
        # Full table scan + Python-side double groupby on economic (indicator_id, frequency)
        econ_groups: dict = {}
        for r in econ_rows:
            key = (r.indicator_id, r.frequency)
            econ_groups[key] = econ_groups.get(key, 0) + 1
        total += len(econ_groups)
        return total

    def teardown(self):
        self.cluster.shutdown()


# ---- ScyllaDB (scylla-driver with shard awareness) --------------------------

class ScyllaDBAdapter(DBAdapter):
    name = "ScyllaDB"

    def setup(self, fundamentals, economic):
        from cassandra.cluster import Cluster
        from cassandra.concurrent import execute_concurrent_with_args
        from cassandra.policies import TokenAwarePolicy, RoundRobinPolicy
        from datetime import datetime

        # scylla-driver is a drop-in replacement for cassandra-driver with
        # Scylla-specific shard awareness: TokenAwarePolicy routes each query
        # directly to the shard owning the partition, reducing cross-node hops.
        #
        # ScyllaDB is started with --broadcast-rpc-address 127.0.0.1 so it
        # advertises the host-reachable address to the driver, enabling full
        # shard-aware port connections even inside Docker.
        self.cluster = Cluster(
            ["127.0.0.1"],
            port=9043,
            load_balancing_policy=TokenAwarePolicy(RoundRobinPolicy()),
        )
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
        fund_params = [list(r.values()) for r in fundamentals]
        econ_params = [
            [r["indicator_id"], r["frequency"], datetime.fromisoformat(r["timestamp"]),
             r["value"], r["revision_number"]]
            for r in economic
        ]
        execute_concurrent_with_args(self.session, fund_stmt, fund_params, concurrency=200)
        execute_concurrent_with_args(self.session, econ_stmt, econ_params, concurrency=200)

    def simple_query_fundamentals(self):
        rows = self.session.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals "
            "WHERE pe_ratio < 20 AND revenue > 1e10 ALLOW FILTERING"
        )
        return len(list(rows))

    def simple_query_economic(self):
        rows = self.session.execute(
            "SELECT indicator_id, timestamp, value, revision_number FROM economic_indicators LIMIT 100"
        )
        return len(list(rows))

    def complex_query_workload(self):
        from cassandra.concurrent import execute_concurrent
        from cassandra.query import SimpleStatement

        total = 0
        # Execute all 3 queries concurrently using execute_concurrent()
        statements = [
            (SimpleStatement("SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals"), ()),
            (SimpleStatement(
                "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals "
                "WHERE gross_margin > 0.3 AND roe > 0.05 ALLOW FILTERING"
            ), ()),
            (SimpleStatement("SELECT indicator_id, frequency, value FROM economic_indicators"), ()),
        ]
        results = execute_concurrent(self.session, statements, concurrency=3, raise_on_first_error=True)
        # Collect results
        rows = list(results[0][1])
        result = list(results[1][1])
        econ_rows = list(results[2][1])
        # Full table scan + Python-side double groupby (symbol, year)
        groups_sy: dict = {}
        for r in rows:
            key = (r.symbol, r.period[:4])
            groups_sy[key] = groups_sy.get(key, 0) + 1
        total += len(groups_sy)
        total += len(result)
        # Full table scan + Python-side double groupby on economic (indicator_id, frequency)
        econ_groups: dict = {}
        for r in econ_rows:
            key = (r.indicator_id, r.frequency)
            econ_groups[key] = econ_groups.get(key, 0) + 1
        total += len(econ_groups)
        return total

    def teardown(self):
        self.cluster.shutdown()


# ---- ClickHouse -------------------------------------------------------------

class ClickHouseAdapter(DBAdapter):
    name = "ClickHouse"

    def setup(self, fundamentals, economic):
        import clickhouse_connect
        from datetime import datetime

        self.client = clickhouse_connect.get_client(
            host="localhost", port=8123, username="default", password="bench",
            settings={"max_threads": 2},
        )
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
        cols_e = list(economic[0].keys())
        ts_idx = cols_e.index("timestamp")
        data_e = [
            [datetime.fromisoformat(v) if i == ts_idx else v for i, v in enumerate(r.values())]
            for r in economic
        ]

        # clickhouse_connect client is thread-safe; split each dataset in half
        # and insert both halves concurrently with 2 threads.
        def _ins_fund(rows):
            self.client.insert("bench.fundamentals", rows, column_names=cols_f)

        def _ins_econ(rows):
            self.client.insert("bench.economic_indicators", rows, column_names=cols_e)

        mid_f = len(data_f) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_fund, data_f[:mid_f]), ex.submit(_ins_fund, data_f[mid_f:])]
            for f in fs:
                f.result()

        mid_e = len(data_e) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_econ, data_e[:mid_e]), ex.submit(_ins_econ, data_e[mid_e:])]
            for f in fs:
                f.result()

    def simple_query_fundamentals(self):
        result = self.client.query(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM bench.fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        return result.row_count

    def simple_query_economic(self):
        result = self.client.query("""
            SELECT indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM bench.economic_indicators
            ) WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
        """)
        return result.row_count

    def complex_query_workload(self):
        total = 0
        # Double groupby: aggregate by symbol and year extracted from period
        r = self.client.query("""
            SELECT symbol, substring(period, 1, 4) AS yr,
                   AVG(revenue) AS avg_rev, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe
            FROM bench.fundamentals
            GROUP BY symbol, yr
            ORDER BY symbol, yr
        """)
        total += r.row_count
        # Full table scan: unindexed multi-column filter
        r = self.client.query(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM bench.fundamentals WHERE gross_margin > 0.3 AND roe > 0.05"
        )
        total += r.row_count
        # Double groupby on economic data
        r = self.client.query("""
            SELECT indicator_id, frequency,
                   AVG(value) AS avg_val, COUNT(*) AS cnt,
                   MIN(value) AS min_val, MAX(value) AS max_val
            FROM bench.economic_indicators
            GROUP BY indicator_id, frequency
        """)
        total += r.row_count
        return total

    def teardown(self):
        self.client.close()


# ---- TimescaleDB (PostgreSQL extension) -------------------------------------

class TimescaleDBAdapter(DBAdapter):
    name = "TimescaleDB"

    def setup(self, fundamentals, economic):
        import psycopg2
        from psycopg2.extras import execute_values

        self.con = psycopg2.connect(
            host="localhost", port=5433, user="bench", password="bench", dbname="bench"
        )
        self.con.autocommit = True
        cur = self.con.cursor()
        cur.execute("SET work_mem = '256MB'")
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
        # Use 2 threads for parallel bulk inserts (psycopg2 connections are thread-safe
        # when each thread owns its own connection)
        _ts_params = dict(host="localhost", port=5433, user="bench", password="bench", dbname="bench")

        def _ins_fund(rows):
            import psycopg2
            from psycopg2.extras import execute_values
            c = psycopg2.connect(**_ts_params)
            c.autocommit = True
            execute_values(c.cursor(), "INSERT INTO fundamentals VALUES %s",
                           [tuple(r.values()) for r in rows], page_size=1000)
            c.close()

        def _ins_econ(rows):
            import psycopg2
            from psycopg2.extras import execute_values
            c = psycopg2.connect(**_ts_params)
            c.autocommit = True
            execute_values(c.cursor(), "INSERT INTO economic_indicators VALUES %s",
                           [tuple(r.values()) for r in rows], page_size=1000)
            c.close()

        mid_f = len(fundamentals) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_fund, fundamentals[:mid_f]), ex.submit(_ins_fund, fundamentals[mid_f:])]
            for f in fs:
                f.result()

        mid_e = len(economic) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_econ, economic[:mid_e]), ex.submit(_ins_econ, economic[mid_e:])]
            for f in fs:
                f.result()

        cur = self.con.cursor()
        cur.execute("CREATE INDEX idx_fund_pe_rev ON fundamentals (pe_ratio, revenue)")
        cur.execute("CREATE INDEX idx_fund_gm_roe ON fundamentals (gross_margin, roe)")
        cur.execute("CREATE INDEX idx_econ_rev ON economic_indicators (indicator_id, timestamp, revision_number DESC)")
        cur.execute("ANALYZE fundamentals")
        cur.execute("ANALYZE economic_indicators")

    def simple_query_fundamentals(self):
        cur = self.con.cursor()
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE pe_ratio < 20 AND revenue > 1e10 ORDER BY pe_ratio"
        )
        return len(cur.fetchall())

    def simple_query_economic(self):
        cur = self.con.cursor()
        cur.execute("""
            SELECT indicator_id, timestamp, value FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY indicator_id, timestamp ORDER BY revision_number DESC
                ) AS rn FROM economic_indicators
            ) sub WHERE rn = 1 ORDER BY timestamp DESC LIMIT 100
        """)
        return len(cur.fetchall())

    def complex_query_workload(self):
        cur = self.con.cursor()
        total = 0
        # Double groupby: aggregate by symbol and year extracted from period
        cur.execute("""
            SELECT symbol, SUBSTR(period, 1, 4) AS yr,
                   AVG(revenue) AS avg_rev, AVG(eps) AS avg_eps, AVG(pe_ratio) AS avg_pe
            FROM fundamentals
            GROUP BY symbol, SUBSTR(period, 1, 4)
            ORDER BY symbol, yr
        """)
        total += len(cur.fetchall())
        # Full table scan: unindexed multi-column filter
        cur.execute(
            "SELECT symbol, period, revenue, eps, pe_ratio FROM fundamentals WHERE gross_margin > 0.3 AND roe > 0.05"
        )
        total += len(cur.fetchall())
        # Double groupby on economic data
        cur.execute("""
            SELECT indicator_id, frequency,
                   AVG(value) AS avg_val, COUNT(*) AS cnt,
                   MIN(value) AS min_val, MAX(value) AS max_val
            FROM economic_indicators
            GROUP BY indicator_id, frequency
        """)
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

        # redis-py is thread-safe; split each dataset in half and insert both
        # halves concurrently. Each thread creates its own pipeline.
        def _ins_fund(rows):
            pipe = self.r.pipeline()
            for row in rows:
                key = f"fund:{row['symbol']}:{row['period']}"
                pipe.hset(key, mapping={k: str(v) for k, v in row.items()})
                pipe.sadd("fund:index", key)
            pipe.execute()

        def _ins_econ(rows):
            pipe = self.r.pipeline()
            for row in rows:
                key = f"econ:{row['indicator_id']}:{row['timestamp']}:{row['revision_number']}"
                pipe.hset(key, mapping={k: str(v) for k, v in row.items()})
                pipe.sadd("econ:index", key)
            pipe.execute()

        mid_f = len(fundamentals) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_fund, fundamentals[:mid_f]), ex.submit(_ins_fund, fundamentals[mid_f:])]
            for f in fs:
                f.result()

        mid_e = len(economic) // 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs = [ex.submit(_ins_econ, economic[:mid_e]), ex.submit(_ins_econ, economic[mid_e:])]
            for f in fs:
                f.result()

        self._fundamentals = fundamentals
        self._economic = economic

    def simple_query_fundamentals(self):
        keys = list(self.r.smembers("fund:index"))
        if not keys:
            return 0
        pipe = self.r.pipeline()
        for key in keys:
            pipe.hgetall(key)
        all_data = pipe.execute()
        return len([d for d in all_data if float(d.get("pe_ratio", 999)) < 20 and float(d.get("revenue", 0)) > 1e10])

    def simple_query_economic(self):
        keys = list(self.r.smembers("econ:index"))[:100]
        if not keys:
            return 0
        pipe = self.r.pipeline()
        for key in keys:
            pipe.hgetall(key)
        return len(pipe.execute())

    def complex_query_workload(self):
        total = 0
        # Full table scan of all fundamentals
        fund_keys = list(self.r.smembers("fund:index"))
        all_fund = []
        if fund_keys:
            pipe = self.r.pipeline()
            for key in fund_keys:
                pipe.hgetall(key)
            all_fund = pipe.execute()
        # Double groupby in Python: (symbol, year)
        groups_sy: dict = {}
        for d in all_fund:
            sym = d.get("symbol", "")
            period = d.get("period", "")
            yr = period[:4] if period else ""
            groups_sy[(sym, yr)] = groups_sy.get((sym, yr), 0) + 1
        total += len(groups_sy)
        # Full table scan filter (gross_margin > 0.3 AND roe > 0.05)
        total += len([
            d for d in all_fund
            if float(d.get("gross_margin", 0)) > 0.3 and float(d.get("roe", -999)) > 0.05
        ])
        # Full table scan + double groupby on economic (indicator_id, frequency)
        econ_keys = list(self.r.smembers("econ:index"))
        all_econ = []
        if econ_keys:
            pipe = self.r.pipeline()
            for key in econ_keys:
                pipe.hgetall(key)
            all_econ = pipe.execute()
        econ_groups: dict = {}
        for d in all_econ:
            key = (d.get("indicator_id", ""), d.get("frequency", ""))
            econ_groups[key] = econ_groups.get(key, 0) + 1
        total += len(econ_groups)
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
        resp = requests.put(
            f"{self.base_url}/admin/databases",
            json={
                "DatabaseName": self.db_name,
                "ReplicationFactor": 1,
            },
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        # Batch insert fundamentals in chunks using 2 threads (HTTP requests are I/O-bound
        # and benefit from concurrent execution; each thread uses its own requests session)
        batch_size = 500
        fund_batches = [
            [
                {
                    "Type": "PUT",
                    "Id": f"fundamentals/{r['symbol']}-{r['period']}",
                    "Document": r,
                    "ChangeVector": None,
                }
                for r in fundamentals[i:i + batch_size]
            ]
            for i in range(0, len(fundamentals), batch_size)
        ]
        econ_batches = [
            [
                {
                    "Type": "PUT",
                    "Id": f"economic/{r['indicator_id']}-{r['timestamp']}-{r['revision_number']}",
                    "Document": r,
                    "ChangeVector": None,
                }
                for r in economic[i:i + batch_size]
            ]
            for i in range(0, len(economic), batch_size)
        ]

        def _send_batch(commands):
            resp = requests.post(
                f"{self.base_url}/databases/{self.db_name}/bulk_docs",
                json={"Commands": commands},
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            resp.raise_for_status()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            futures = [ex.submit(_send_batch, b) for b in fund_batches + econ_batches]
            for f in futures:
                f.result()

    def _fetch_all_docs(self, prefix: str, page_size: int = 1024) -> list:
        """Paginate through all documents matching a prefix."""
        import requests

        all_docs: list = []
        start = 0
        while True:
            resp = requests.get(
                f"{self.base_url}/databases/{self.db_name}/docs",
                params={"startsWith": prefix, "start": start, "pageSize": page_size},
                timeout=30,
            )
            batch = resp.json().get("Results", [])
            all_docs.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
        return all_docs

    def simple_query_fundamentals(self):
        results = self._fetch_all_docs("fundamentals/")
        return len([r for r in results if r.get("pe_ratio", 999) < 20 and r.get("revenue", 0) > 1e10])

    def simple_query_economic(self):
        results = self._fetch_all_docs("economic/")
        # Get latest revision per (indicator_id, timestamp), sort by timestamp desc, limit 100
        latest: dict = {}
        for r in results:
            key = (r.get("indicator_id", ""), r.get("timestamp", ""))
            rev = r.get("revision_number", 0)
            if key not in latest or rev > latest[key]["revision_number"]:
                latest[key] = r
        sorted_vals = sorted(latest.values(), key=lambda x: x.get("timestamp", ""), reverse=True)
        return len(sorted_vals[:100])

    def complex_query_workload(self):
        total = 0
        # Fetch all fundamentals (double groupby done client-side)
        fund_docs = self._fetch_all_docs("fundamentals/")
        # Double groupby: (symbol, year)
        groups_sy: dict = {}
        for d in fund_docs:
            sym = d.get("symbol", "")
            period = d.get("period", "")
            yr = period[:4] if period else ""
            groups_sy[(sym, yr)] = groups_sy.get((sym, yr), 0) + 1
        total += len(groups_sy)
        # Full table scan filter
        total += len([
            d for d in fund_docs
            if float(d.get("gross_margin", 0)) > 0.3 and float(d.get("roe", -999)) > 0.05
        ])
        # Fetch all economic data + double groupby (indicator_id, frequency)
        econ_docs = self._fetch_all_docs("economic/")
        econ_groups: dict = {}
        for d in econ_docs:
            key = (d.get("indicator_id", ""), d.get("frequency", ""))
            econ_groups[key] = econ_groups.get(key, 0) + 1
        total += len(econ_groups)
        return total


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_ADAPTERS: list[type[DBAdapter]] = [
    DuckDBAdapter,
    SQLiteAdapter,
    PostgreSQLAdapter,
    MySQLAdapter,
    MSSQLPythonAdapter,
    PyODBCMSSQLAdapter,
    MongoDBAdapter,
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
        print(f"\n[BENCHMARK ERROR] {db} – data_load: {exc}", flush=True)
        traceback.print_exc()
        results.append(BenchResult(db, "data_load", 0, error=str(exc)))
        return results

    # Simple query: fundamentals screening
    for i in range(iterations):
        try:
            with _timer() as t:
                n = adapter.simple_query_fundamentals()
            results.append(BenchResult(db, "simple_query_fundamentals", t[0], n))
        except Exception as exc:
            print(f"\n[BENCHMARK ERROR] {db} – simple_query_fundamentals: {exc}", flush=True)
            traceback.print_exc()
            results.append(BenchResult(db, "simple_query_fundamentals", 0, error=str(exc)))

    # Simple query: economic latest values
    for i in range(iterations):
        try:
            with _timer() as t:
                n = adapter.simple_query_economic()
            results.append(BenchResult(db, "simple_query_economic", t[0], n))
        except Exception as exc:
            print(f"\n[BENCHMARK ERROR] {db} – simple_query_economic: {exc}", flush=True)
            traceback.print_exc()
            results.append(BenchResult(db, "simple_query_economic", 0, error=str(exc)))

    # Complex query workload
    for i in range(iterations):
        try:
            with _timer() as t:
                n = adapter.complex_query_workload()
            results.append(BenchResult(db, "complex_query_workload", t[0], n))
        except Exception as exc:
            print(f"\n[BENCHMARK ERROR] {db} – complex_query_workload: {exc}", flush=True)
            traceback.print_exc()
            results.append(BenchResult(db, "complex_query_workload", 0, error=str(exc)))

    try:
        adapter.teardown()
    except Exception:
        pass

    return results


def generate_summary(all_results: list[BenchResult], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # Group results
    ops = ["data_load", "simple_query_fundamentals", "simple_query_economic", "complex_query_workload"]
    op_labels = {
        "data_load": "Data Load",
        "simple_query_fundamentals": "Simple Query: Fundamentals Screening",
        "simple_query_economic": "Simple Query: Economic Latest Values",
        "complex_query_workload": "Complex Query Workload",
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
    lines.append(f"- **Fundamentals records**: 500 symbols × 200 periods = 100,000 rows")
    lines.append(f"- **Economic records**: 250 indicators × 200 months (~100,000 rows)\n")

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
                # Escape pipe characters and collapse newlines so the error
                # message doesn't break Markdown table cell rendering.
                err_cell = errors[0].error[:200].replace("\n", " ").replace("|", "&#124;")
                lines.append(f"| {db} | — | — | — | — | {err_cell} |")
                continue
            times = [r.elapsed_ms for r in op_results]
            avg = statistics.mean(times)
            mn = min(times)
            mx = max(times)
            row_count = op_results[0].row_count
            lines.append(f"| {db} | {avg:.2f} | {mn:.2f} | {mx:.2f} | {row_count} | — |")

        lines.append("")

    # Full error log section — use HTML <pre><code> blocks to avoid premature
    # fence termination when error messages contain backtick sequences.
    all_errors = [r for r in all_results if r.error]
    if all_errors:
        lines.append("## Full Error Log\n")
        for r in all_errors:
            lines.append(f"### {r.db_name} – {r.operation}\n")
            escaped = r.error.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"<pre><code>{escaped}</code></pre>\n")

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
