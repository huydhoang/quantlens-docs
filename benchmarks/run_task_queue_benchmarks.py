#!/usr/bin/env python3
"""
Task Queue Benchmark Runner
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Benchmarks 10 task queue configurations across throughput, latency, reliability,
and long-running backtest simulation scenarios.

Packages under test:
  Distributed Task Queues:
    1.  Celery         — enterprise standard (Redis broker)
    2.  RQ             — simple Redis-only queue
    3.  BullMQ         — cross-platform (Node.js/Python), high performance
    4.  Huey (Redis)   — lightweight, Redis backend
    5.  Huey (SQLite)  — lightweight, SQLite backend (no Redis required)
    6.  Dramatiq       — modern, middleware-based
    7.  ARQ            — async-first, type-hinted
    8.  TaskTiger      — Redis-based, Close.io origin
    9.  Taskiq         — Redis Streams, fastest performance
    10. Procrastinate  — PostgreSQL-based (no Redis required)

Excluded:
  - APScheduler — in-process scheduler, not a distributed task queue
  - Rocketry — unmaintained since December 2022, incompatible with Pydantic v2

Usage:
    python benchmarks/run_task_queue_benchmarks.py
    python benchmarks/run_task_queue_benchmarks.py --queues celery rq huey
    python benchmarks/run_task_queue_benchmarks.py --scenarios enqueue-burst
"""

import argparse
import asyncio
import importlib.util
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

# ── Project root ─────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Ensure the project root is on sys.path so that module-level task
# functions (e.g. ``benchmarks.run_task_queue_benchmarks._bench_flaky``)
# are importable by workers that resolve functions by dotted path (RQ
# SimpleWorker, TaskTiger Worker).
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Queue package metadata ───────────────────────────────────────────

QUEUES = [
    # ── Distributed Task Queues ──────────────────────────────────────
    {
        "id": "celery",
        "name": "Celery",
        "category": "Distributed Task Queue",
        "pip": "celery[redis]",
        "import": "celery",
        "broker": "redis",
        "description": "Enterprise standard — canvas workflows, Flower monitoring",
    },
    {
        "id": "rq",
        "name": "RQ",
        "category": "Distributed Task Queue",
        "pip": "rq",
        "import": "rq",
        "broker": "redis",
        "description": "Simple Redis-only queue — minimal configuration",
    },
    {
        "id": "bullmq",
        "name": "BullMQ",
        "category": "Distributed Task Queue",
        "pip": "bullmq",
        "import": "bullmq",
        "broker": "redis",
        "description": "Cross-platform (Node.js/Python) — high performance",
    },
    {
        "id": "huey",
        "name": "Huey (Redis)",
        "category": "Distributed Task Queue",
        "pip": "huey",
        "import": "huey",
        "broker": "redis",
        "description": "Lightweight — Redis backend",
    },
    {
        "id": "huey-sqlite",
        "name": "Huey (SQLite)",
        "category": "Distributed Task Queue",
        "pip": "huey",
        "import": "huey",
        "broker": "sqlite",
        "description": "Lightweight — SQLite backend, no external broker required",
    },
    {
        "id": "dramatiq",
        "name": "Dramatiq",
        "category": "Distributed Task Queue",
        "pip": "dramatiq[redis]",
        "import": "dramatiq",
        "broker": "redis",
        "description": "Modern, reliable — middleware-based architecture",
    },
    {
        "id": "arq",
        "name": "ARQ",
        "category": "Distributed Task Queue",
        "pip": "arq",
        "import": "arq",
        "broker": "redis",
        "description": "Async-first, type-hinted — by Samuel Colvin (Pydantic author)",
    },
    {
        "id": "tasktiger",
        "name": "TaskTiger",
        "category": "Distributed Task Queue",
        "pip": "tasktiger",
        "import": "tasktiger",
        "broker": "redis",
        "description": "Redis-based — Close.io origin, reliable execution",
    },
    {
        "id": "taskiq",
        "name": "Taskiq",
        "category": "Distributed Task Queue",
        "pip": "taskiq[redis]",
        "import": "taskiq",
        "broker": "redis",
        "description": "Fastest performance — Redis Streams, native async",
    },
    {
        "id": "procrastinate",
        "name": "Procrastinate",
        "category": "Distributed Task Queue",
        "pip": "procrastinate[aiopg]",
        "import": "procrastinate",
        "broker": "postgresql",
        "description": "PostgreSQL-based — no Redis required",
    },
]

# ── Benchmark scenarios ──────────────────────────────────────────────

SCENARIOS = [
    {
        "name": "enqueue-burst",
        "label": "Burst enqueue — 1 000 tasks",
        "desc": "Time to enqueue 1 000 no-op tasks (measures broker write throughput)",
        "task": "noop",
        "count": 1000,
    },
    {
        "name": "enqueue-heavy",
        "label": "Heavy enqueue — 10 000 tasks",
        "desc": "Time to enqueue 10 000 no-op tasks (throughput ceiling)",
        "task": "noop",
        "count": 10000,
    },
    {
        "name": "roundtrip-small",
        "label": "Round-trip latency — 100 tasks",
        "desc": "Enqueue + wait for completion of 100 lightweight tasks (end-to-end latency)",
        "task": "noop",
        "count": 100,
        "wait": True,
    },
    {
        "name": "roundtrip-cpu",
        "label": "Round-trip CPU — 50 tasks",
        "desc": "Enqueue + wait for 50 CPU-bound tasks (simulates backtest dispatch)",
        "task": "cpu_work",
        "count": 50,
        "wait": True,
    },
    {
        "name": "retry-reliability",
        "label": "Retry reliability — 20 failing tasks",
        "desc": "Tasks that fail once then succeed — measures retry correctness",
        "task": "flaky",
        "count": 20,
        "wait": True,
    },
    {
        "name": "backtest-sim",
        "label": "Backtest simulation — 3 long-running tasks",
        "desc": "Enqueue 3 tasks that each simulate a ~5 s NautilusTrader backtest (CPU-bound). "
                "Measures dispatch overhead for long-running jobs — the primary QuantLens workload.",
        "task": "backtest_sim",
        "count": 3,
        "wait": True,
    },
]

# ── Import availability check ────────────────────────────────────────


def check_import(module_name: str) -> bool:
    """Return True if the package can be imported."""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ValueError, ModuleNotFoundError):
        return False


# ── Module-level task functions for RQ & TaskTiger ───────────────────
# RQ and TaskTiger store function references by importable path.
# Defining these at module level (rather than inside bench_*) and setting
# __module__ to the package-relative name allows enqueue to succeed even
# when the script is executed directly via ``python …/run_task_queue_benchmarks.py``.
# Workers are not started during most benchmarks, so only enqueue throughput
# is measured; round-trip scenarios will time-out gracefully.  The
# retry-reliability scenario is the exception: backends that support
# in-process or burst-mode workers (Celery eager, RQ SimpleWorker,
# Huey immediate) start a worker so that retry semantics are validated.

_MODULE_PATH = "benchmarks.run_task_queue_benchmarks"


def _bench_noop():
    """No-op task for enqueue benchmarks."""
    pass


def _bench_cpu_work():
    """CPU-bound task for throughput benchmarks."""
    return sum(i * i for i in range(10_000))


def _bench_backtest_sim():
    """Simulate a NautilusTrader backtest (~5 s of CPU work).

    Uses iterative floating-point math to approximate the CPU profile of a
    real backtest: indicator calculation, order matching, and portfolio
    accounting.  The loop count is calibrated to run ~5 s on a 2-core
    GitHub Actions runner.
    """
    total = 0.0
    for i in range(25_000_000):
        total += (i * 0.0001) ** 0.5
    return total


_bench_noop.__module__ = _MODULE_PATH
_bench_cpu_work.__module__ = _MODULE_PATH
_bench_backtest_sim.__module__ = _MODULE_PATH

# ── Flaky task infrastructure ────────────────────────────────────────
# Tracks per-task attempt counts in Redis (cross-process) so a flaky task
# can fail on the first attempt and succeed on the retry.  A module-level
# dict is the fallback when Redis is unavailable (e.g. Huey-SQLite).

_flaky_attempts: dict = {}
_flaky_lock = threading.Lock()
_flaky_redis_client = None


def _get_flaky_redis_client():
    """Return a cached Redis client for flaky-task attempt tracking."""
    global _flaky_redis_client
    if _flaky_redis_client is None:
        import redis as _redis
        _flaky_redis_client = _redis.Redis(host="localhost", port=6379)
    return _flaky_redis_client


def _flaky_check(task_id: str) -> None:
    """Raise on the first call for *task_id*; succeed on subsequent calls."""
    try:
        r = _get_flaky_redis_client()
        key = f"bench:flaky:{task_id}"
        attempt = r.incr(key)
        r.expire(key, 300)
    except ImportError:
        # Fallback for non-Redis backends (Huey-SQLite)
        with _flaky_lock:
            _flaky_attempts[task_id] = _flaky_attempts.get(task_id, 0) + 1
            attempt = _flaky_attempts[task_id]
    if attempt == 1:
        raise RuntimeError(f"transient benchmark failure (task_id={task_id})")


def _bench_flaky(task_id: str):
    """Module-level flaky task for RQ / TaskTiger.

    Fails on first invocation for a given *task_id* and succeeds when
    the queue backend retries it with the same arguments.
    """
    _flaky_check(task_id)


_bench_flaky.__module__ = _MODULE_PATH


def _cleanup_flaky_keys():
    """Remove flaky attempt-tracking keys before a retry-reliability run."""
    try:
        r = _get_flaky_redis_client()
        for key in r.scan_iter("bench:flaky:*"):
            r.delete(key)
    except ImportError:
        pass
    with _flaky_lock:
        _flaky_attempts.clear()


def _count_flaky_completions() -> int:
    """Count flaky tasks that completed successfully (attempt >= 2 in Redis).

    ``_flaky_check`` uses ``INCR`` on a per-task Redis key.  A value of 1
    means the first (failing) attempt was recorded; a value >= 2 means the
    retry succeeded.  Falls back to the in-memory dict for non-Redis
    backends (e.g. Huey-SQLite).
    """
    count = 0
    try:
        r = _get_flaky_redis_client()
        keys = list(r.scan_iter("bench:flaky:*"))
        if keys:
            values = r.mget(keys)
            count = sum(1 for v in values if v and int(v) >= 2)
    except ImportError:
        with _flaky_lock:
            count = sum(1 for v in _flaky_attempts.values() if v >= 2)
    return count


# ── Benchmark implementations ────────────────────────────────────────


def _redis_available() -> bool:
    """Quick check that Redis is reachable on localhost:6379."""
    try:
        import socket
        s = socket.create_connection(("127.0.0.1", 6379), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def _postgres_available() -> bool:
    """Quick check that PostgreSQL is reachable on localhost:5432."""
    try:
        import socket
        s = socket.create_connection(("127.0.0.1", 5432), timeout=1)
        s.close()
        return True
    except OSError:
        return False



def _time_enqueue(fn, count: int) -> dict:
    """Run fn(count) and return timing metrics."""
    start = time.perf_counter()
    fn(count)
    elapsed = time.perf_counter() - start
    return {
        "count": count,
        "elapsed_s": round(elapsed, 4),
        "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
    }


# ── Celery ───────────────────────────────────────────────────────────

def bench_celery(scenario: dict) -> dict:
    from celery import Celery

    app = Celery(broker="redis://localhost:6379/0", backend="redis://localhost:6379/1")

    @app.task
    def noop():
        pass

    @app.task
    def cpu_work():
        total = sum(i * i for i in range(10_000))
        return total

    @app.task(bind=True, max_retries=1, default_retry_delay=0)
    def flaky(self, task_id):
        if self.request.retries == 0:
            raise self.retry(
                exc=RuntimeError("transient benchmark failure"))

    @app.task
    def backtest_sim():
        return _bench_backtest_sim()

    task_fn = {"noop": noop, "cpu_work": cpu_work, "flaky": flaky,
               "backtest_sim": backtest_sim}[scenario["task"]]
    count = scenario["count"]
    is_flaky = scenario["task"] == "flaky"

    if scenario.get("wait"):
        # For retry-reliability, enable eager mode so retries execute
        # in-process without requiring a separate Celery worker.
        if is_flaky:
            app.conf.task_always_eager = True
            app.conf.task_eager_propagates = False
            _cleanup_flaky_keys()

        start = time.perf_counter()
        results = [task_fn.delay(str(uuid.uuid4())) if is_flaky
                   else task_fn.delay()
                   for _ in range(count)]
        elapsed = time.perf_counter() - start

        if is_flaky:
            completed = sum(1 for r in results if r.successful())
            return {
                "count": count,
                "elapsed_s": round(elapsed, 4),
                "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
                "completed": completed,
                "timed_out": count - completed,
            }

        deadline = time.time() + 60
        pending = list(results)
        while pending and time.time() < deadline:
            pending = [r for r in pending if not r.ready()]
            time.sleep(0.05)
        return {
            "count": count,
            "elapsed_s": round(elapsed, 4),
            "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
            "completed": count - len(pending),
            "timed_out": len(pending),
        }

    def enqueue(n):
        for _ in range(n):
            if is_flaky:
                task_fn.delay(str(uuid.uuid4()))
            else:
                task_fn.delay()

    return _time_enqueue(enqueue, count)


# ── RQ ───────────────────────────────────────────────────────────────

def bench_rq(scenario: dict) -> dict:
    import redis
    from rq import Queue, Retry, SimpleWorker

    conn = redis.Redis(host="localhost", port=6379)
    q = Queue(connection=conn)

    fn_map = {"noop": _bench_noop, "cpu_work": _bench_cpu_work, "flaky": _bench_flaky,
              "backtest_sim": _bench_backtest_sim}
    fn = fn_map[scenario["task"]]
    count = scenario["count"]
    is_flaky = scenario["task"] == "flaky"

    if scenario.get("wait"):
        if is_flaky:
            _cleanup_flaky_keys()
        start = time.perf_counter()
        jobs = []
        for _ in range(count):
            if is_flaky:
                jobs.append(q.enqueue(fn, str(uuid.uuid4()),
                                      retry=Retry(max=1, interval=0)))
            else:
                jobs.append(q.enqueue(fn))
        # For retry-reliability, run an in-process SimpleWorker so tasks
        # (including retries) actually execute.  Burst mode exits after one
        # pass, so we loop until all retries have been processed.
        if is_flaky:
            w = SimpleWorker([q], connection=conn)
            burst_deadline = time.monotonic() + 30
            while time.monotonic() < burst_deadline:
                w.work(burst=True)
                if _count_flaky_completions() >= count:
                    break
                time.sleep(0.1)
        elapsed = time.perf_counter() - start
        deadline = time.time() + 60
        while any(not j.is_finished for j in jobs) and time.time() < deadline:
            time.sleep(0.05)
        return {
            "count": count,
            "elapsed_s": round(elapsed, 4),
            "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
            "completed": sum(1 for j in jobs if j.is_finished),
            "timed_out": sum(1 for j in jobs if not j.is_finished),
        }

    def enqueue(n):
        for _ in range(n):
            if is_flaky:
                q.enqueue(fn, str(uuid.uuid4()), retry=Retry(max=1, interval=0))
            else:
                q.enqueue(fn)

    return _time_enqueue(enqueue, count)


# ── Huey (Redis) ─────────────────────────────────────────────────────

def bench_huey(scenario: dict) -> dict:
    from huey import RedisHuey

    is_flaky = scenario["task"] == "flaky"
    # For retry-reliability, use immediate mode so retries execute
    # in-process without a separate huey_consumer.
    huey = RedisHuey("bench", host="localhost", port=6379,
                     immediate=is_flaky)

    @huey.task()
    def noop_task():
        pass

    @huey.task()
    def cpu_task():
        return sum(i * i for i in range(10_000))

    @huey.task(retries=1, retry_delay=0)
    def flaky_task(task_id):
        _flaky_check(task_id)

    @huey.task()
    def backtest_sim_task():
        return _bench_backtest_sim()

    task_map = {
        "noop": noop_task,
        "cpu_work": cpu_task,
        "flaky": flaky_task,
        "backtest_sim": backtest_sim_task,
    }
    task_fn = task_map[scenario["task"]]
    count = scenario["count"]

    if is_flaky:
        _cleanup_flaky_keys()
        start = time.perf_counter()
        completed = 0
        for _ in range(count):
            try:
                task_fn(str(uuid.uuid4()))
                completed += 1
            except Exception:
                pass
        elapsed = time.perf_counter() - start
        return {
            "count": count,
            "elapsed_s": round(elapsed, 4),
            "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
            "completed": completed,
        }

    def enqueue(n):
        for _ in range(n):
            task_fn()

    metrics = _time_enqueue(enqueue, count)
    return metrics


# ── Huey (SQLite) ────────────────────────────────────────────────────

def bench_huey_sqlite(scenario: dict) -> dict:
    import tempfile
    from huey import SqliteHuey

    # Use a unique temp file per run to avoid cross-run state contamination
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="huey_bench_")
    os.close(fd)
    try:
        is_flaky = scenario["task"] == "flaky"
        huey = SqliteHuey("bench-sqlite", filename=db_path,
                          immediate=is_flaky)

        @huey.task()
        def noop_task():
            pass

        @huey.task()
        def cpu_task():
            return sum(i * i for i in range(10_000))

        @huey.task(retries=1, retry_delay=0)
        def flaky_task(task_id):
            _flaky_check(task_id)

        @huey.task()
        def backtest_sim_task():
            return _bench_backtest_sim()

        task_map = {
            "noop": noop_task,
            "cpu_work": cpu_task,
            "flaky": flaky_task,
            "backtest_sim": backtest_sim_task,
        }
        task_fn = task_map[scenario["task"]]
        count = scenario["count"]

        if is_flaky:
            _cleanup_flaky_keys()
            start = time.perf_counter()
            completed = 0
            for _ in range(count):
                try:
                    task_fn(str(uuid.uuid4()))
                    completed += 1
                except Exception:
                    pass
            elapsed = time.perf_counter() - start
            return {
                "count": count,
                "elapsed_s": round(elapsed, 4),
                "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
                "completed": completed,
            }

        def enqueue(n):
            for _ in range(n):
                task_fn()

        metrics = _time_enqueue(enqueue, count)
        return metrics
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass


# ── Dramatiq ─────────────────────────────────────────────────────────

def bench_dramatiq(scenario: dict) -> dict:
    import dramatiq

    is_flaky = scenario["task"] == "flaky"
    count = scenario["count"]

    if is_flaky:
        # Use StubBroker + Worker for retry-reliability so tasks execute
        # in-process without a separate ``dramatiq`` worker process.
        from dramatiq.brokers.stub import StubBroker as DramatiqStubBroker
        from dramatiq.worker import Worker as DramatiqWorker

        stub = DramatiqStubBroker()
        dramatiq.set_broker(stub)

        @dramatiq.actor(max_retries=1, min_backoff=0, max_backoff=0)
        def flaky_stub(task_id):
            _flaky_check(task_id)

        _cleanup_flaky_keys()

        worker = DramatiqWorker(stub, worker_threads=1)
        worker.start()

        start = time.perf_counter()
        for _ in range(count):
            flaky_stub.send(str(uuid.uuid4()))
        try:
            stub.join(flaky_stub.queue_name, fail_fast=False, timeout=30_000)
            worker.join()
        except Exception:
            pass
        elapsed = time.perf_counter() - start

        worker.stop()
        completed = _count_flaky_completions()
        return {
            "count": count,
            "elapsed_s": round(elapsed, 4),
            "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
            "completed": completed,
        }

    from dramatiq.brokers.redis import RedisBroker

    broker = RedisBroker(host="localhost", port=6379)
    dramatiq.set_broker(broker)

    @dramatiq.actor
    def noop_actor():
        pass

    @dramatiq.actor
    def cpu_actor():
        return sum(i * i for i in range(10_000))

    @dramatiq.actor
    def backtest_sim_actor():
        return _bench_backtest_sim()

    actor_map = {"noop": noop_actor, "cpu_work": cpu_actor,
                 "backtest_sim": backtest_sim_actor}
    actor = actor_map[scenario["task"]]

    def enqueue(n):
        for _ in range(n):
            actor.send()

    metrics = _time_enqueue(enqueue, count)
    return metrics


# ── ARQ ──────────────────────────────────────────────────────────────

def bench_arq(scenario: dict) -> dict:
    import arq

    async def noop(ctx):
        pass

    async def cpu_work(ctx):
        return sum(i * i for i in range(10_000))

    async def flaky(ctx, task_id):
        """Fail on first attempt; succeed on retry via ``_flaky_check``."""
        _flaky_check(task_id)

    is_flaky = scenario["task"] == "flaky"
    count = scenario["count"]

    if is_flaky:
        _cleanup_flaky_keys()

        async def _run_flaky(n):
            from arq.worker import Worker as ArqWorker

            redis_settings = arq.connections.RedisSettings(
                host="localhost", port=6379)
            redis = await arq.create_pool(redis_settings)

            start = time.perf_counter()
            for _ in range(n):
                await redis.enqueue_job("flaky", str(uuid.uuid4()))

            # Run an in-process ARQ worker to consume the flaky jobs.
            # ``max_tries=2`` allows one retry after the first failure.
            worker = ArqWorker(
                functions=[flaky],
                redis_settings=redis_settings,
                max_tries=2,
            )
            worker_task = asyncio.ensure_future(worker.main())

            # Poll until all tasks have completed or timeout.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if _count_flaky_completions() >= n:
                    break
                await asyncio.sleep(0.5)

            # Allow worker to finish processing current job before cancel.
            await asyncio.sleep(1)
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass

            elapsed = time.perf_counter() - start
            await redis.aclose()
            return elapsed

        elapsed = asyncio.run(_run_flaky(count))
        completed = _count_flaky_completions()
        return {
            "count": count,
            "elapsed_s": round(elapsed, 4),
            "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
            "completed": completed,
        }

    async def _run(n):
        redis = await arq.create_pool(
            arq.connections.RedisSettings(host="localhost", port=6379))
        fn_name = {"noop": "noop", "cpu_work": "cpu_work",
                   "backtest_sim": "backtest_sim"}[scenario["task"]]
        start = time.perf_counter()
        for _ in range(n):
            await redis.enqueue_job(fn_name)
        elapsed = time.perf_counter() - start
        await redis.aclose()
        return elapsed

    elapsed = asyncio.run(_run(count))
    return {
        "count": count,
        "elapsed_s": round(elapsed, 4),
        "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
    }


# ── Taskiq ───────────────────────────────────────────────────────────

def bench_taskiq(scenario: dict) -> dict:
    is_flaky = scenario["task"] == "flaky"
    count = scenario["count"]

    if is_flaky:
        # Use InMemoryBroker so tasks execute in-process (no external
        # worker).  Manual retry mirrors the ``retry_on_error`` semantics.
        from taskiq import InMemoryBroker

        mem_broker = InMemoryBroker()

        @mem_broker.task
        async def flaky_mem(task_id):
            _flaky_check(task_id)

        _cleanup_flaky_keys()

        async def _run_flaky(n):
            await mem_broker.startup()
            start = time.perf_counter()
            completed = 0
            for _ in range(n):
                tid = str(uuid.uuid4())
                result = await flaky_mem.kiq(tid)
                res = await result.wait_result(timeout=10)
                if res.is_err:
                    # Manual retry (mirrors max_retries=1)
                    await asyncio.sleep(0.05)
                    result2 = await flaky_mem.kiq(tid)
                    res2 = await result2.wait_result(timeout=10)
                    if not res2.is_err:
                        completed += 1
                else:
                    completed += 1
            elapsed = time.perf_counter() - start
            await mem_broker.shutdown()
            return elapsed, completed

        elapsed, completed = asyncio.run(_run_flaky(count))
        return {
            "count": count,
            "elapsed_s": round(elapsed, 4),
            "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
            "completed": completed,
        }

    from taskiq_redis import ListQueueBroker

    broker = ListQueueBroker("redis://localhost:6379")

    @broker.task
    async def noop_task():
        pass

    @broker.task
    async def cpu_task():
        return sum(i * i for i in range(10_000))

    @broker.task
    async def backtest_sim_task():
        return _bench_backtest_sim()

    task_map = {"noop": noop_task, "cpu_work": cpu_task,
                "backtest_sim": backtest_sim_task}
    task_fn = task_map[scenario["task"]]

    async def _run(n):
        await broker.startup()
        start = time.perf_counter()
        for _ in range(n):
            await task_fn.kiq()
        elapsed = time.perf_counter() - start
        await broker.shutdown()
        return elapsed

    elapsed = asyncio.run(_run(count))
    return {
        "count": count,
        "elapsed_s": round(elapsed, 4),
        "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
    }


# ── BullMQ ───────────────────────────────────────────────────────────

def bench_bullmq(scenario: dict) -> dict:
    from bullmq import Queue as BullQueue

    count = scenario["count"]
    is_flaky = scenario["task"] == "flaky"

    if is_flaky:
        from bullmq import Worker as BullWorker

        _cleanup_flaky_keys()

        async def _run_flaky(n):
            conn_opts = {"host": "localhost", "port": 6379}
            # Use a dedicated queue name to avoid interference with other
            # scenarios that may still have pending jobs.
            q = BullQueue("bench-flaky", {"connection": conn_opts})

            async def process_job(job, token):
                _flaky_check(job.data.get("task_id"))

            worker = BullWorker("bench-flaky", process_job,
                                {"connection": conn_opts})

            start = time.perf_counter()
            for _ in range(n):
                await q.add("flaky", {"task_id": str(uuid.uuid4())},
                            {"attempts": 2,
                             "backoff": {"type": "fixed", "delay": 0}})

            # Poll until all flaky tasks completed.
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if _count_flaky_completions() >= n:
                    break
                await asyncio.sleep(0.1)

            elapsed = time.perf_counter() - start
            await worker.close()
            await q.close()
            return elapsed

        elapsed = asyncio.run(_run_flaky(count))
        completed = _count_flaky_completions()
        return {
            "count": count,
            "elapsed_s": round(elapsed, 4),
            "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
            "completed": completed,
        }

    async def _run(n):
        q = BullQueue("bench", {"connection": {"host": "localhost", "port": 6379}})
        start = time.perf_counter()
        for _ in range(n):
            await q.add("noop", {})
        elapsed = time.perf_counter() - start
        await q.close()
        return elapsed

    elapsed = asyncio.run(_run(count))
    return {
        "count": count,
        "elapsed_s": round(elapsed, 4),
        "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
    }


# ── TaskTiger ────────────────────────────────────────────────────────

def bench_tasktiger(scenario: dict) -> dict:
    import redis as redislib
    import tasktiger

    conn = redislib.Redis(host="localhost", port=6379)
    tiger = tasktiger.TaskTiger(connection=conn)

    fn_map = {"noop": _bench_noop, "cpu_work": _bench_cpu_work, "flaky": _bench_flaky,
              "backtest_sim": _bench_backtest_sim}
    fn = fn_map[scenario["task"]]
    count = scenario["count"]
    is_flaky = scenario["task"] == "flaky"

    if is_flaky:
        _cleanup_flaky_keys()

        # Enqueue flaky tasks then run the Tiger Worker in burst mode
        # (``once=True``) until all retries complete.
        for _ in range(count):
            tiger.delay(fn, args=[str(uuid.uuid4())],
                        retry_method=tasktiger.fixed(0, 1))

        start = time.perf_counter()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            w = tasktiger.Worker(tiger)
            # Process multiple tasks per iteration to handle both
            # initial failures and their retries in the same loop pass.
            for _ in range(5):
                w.run(once=True)
            if _count_flaky_completions() >= count:
                break
            time.sleep(0.2)
        elapsed = time.perf_counter() - start

        completed = _count_flaky_completions()
        return {
            "count": count,
            "elapsed_s": round(elapsed, 4),
            "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
            "completed": completed,
        }

    def enqueue(n):
        for _ in range(n):
            tiger.delay(fn)

    metrics = _time_enqueue(enqueue, count)
    return metrics


# ── Procrastinate ────────────────────────────────────────────────────

def bench_procrastinate(scenario: dict) -> dict:
    import procrastinate
    from procrastinate.contrib.aiopg import AiopgConnector

    dsn = os.environ.get(
        "PROCRASTINATE_URL", "postgresql://bench:bench@localhost:5432/bench"
    )
    count = scenario["count"]
    is_flaky = scenario["task"] == "flaky"

    if is_flaky:
        _cleanup_flaky_keys()

        async def _run_flaky(n):
            connector = AiopgConnector(dsn=dsn)
            app = procrastinate.App(connector=connector)

            @app.task(retry=1)
            async def flaky_task(task_id):
                _flaky_check(task_id)

            async with app.open_async():
                start = time.perf_counter()
                for _ in range(n):
                    await flaky_task.defer_async(task_id=str(uuid.uuid4()))

                # Run an in-process worker to consume deferred jobs.
                worker_task = asyncio.ensure_future(
                    app.run_worker_async(
                        queues=["default"],
                        install_signal_handlers=False,
                    )
                )

                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if _count_flaky_completions() >= n:
                        break
                    await asyncio.sleep(0.5)

                # Allow worker to finish processing current job before cancel.
                await asyncio.sleep(1)
                worker_task.cancel()
                try:
                    await worker_task
                except asyncio.CancelledError:
                    pass

                elapsed = time.perf_counter() - start
            return elapsed

        elapsed = asyncio.run(_run_flaky(count))
        completed = _count_flaky_completions()
        return {
            "count": count,
            "elapsed_s": round(elapsed, 4),
            "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
            "completed": completed,
        }

    async def _run(n):
        connector = AiopgConnector(dsn=dsn)
        app = procrastinate.App(connector=connector)

        @app.task
        async def noop_task():
            pass

        @app.task
        async def cpu_task():
            return sum(i * i for i in range(10_000))

        @app.task
        async def backtest_sim_task():
            return _bench_backtest_sim()

        task_map = {"noop": noop_task, "cpu_work": cpu_task,
                    "backtest_sim": backtest_sim_task}
        task_fn = task_map[scenario["task"]]

        async with app.open_async():
            start = time.perf_counter()
            for _ in range(n):
                await task_fn.defer_async()
            elapsed = time.perf_counter() - start
        return elapsed

    elapsed = asyncio.run(_run(count))
    return {
        "count": count,
        "elapsed_s": round(elapsed, 4),
        "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
    }


# ── Fallback: record unavailable packages ────────────────────────────

def bench_unavailable(queue: dict, scenario: dict, reason: str) -> dict:
    return {"skipped": True, "reason": reason}


# ── Dispatcher ───────────────────────────────────────────────────────

BENCH_FNS = {
    "celery":        bench_celery,
    "rq":            bench_rq,
    "bullmq":        bench_bullmq,
    "huey":          bench_huey,
    "huey-sqlite":   bench_huey_sqlite,
    "dramatiq":      bench_dramatiq,
    "arq":           bench_arq,
    "tasktiger":     bench_tasktiger,
    "taskiq":        bench_taskiq,
    "procrastinate": bench_procrastinate,
}


def run_queue_scenario(queue: dict, scenario: dict) -> dict:
    qid = queue["id"]
    module = queue["import"]

    if not check_import(module):
        return bench_unavailable(queue, scenario, f"package '{module}' not installed")

    broker = queue["broker"]
    if broker == "redis" and not _redis_available():
        return bench_unavailable(queue, scenario, "Redis not reachable on localhost:6379")

    if broker == "postgresql" and not _postgres_available():
        return bench_unavailable(queue, scenario, "PostgreSQL not reachable on localhost:5432")

    bench_fn = BENCH_FNS.get(qid)
    if bench_fn is None:
        return bench_unavailable(queue, scenario, f"no benchmark implementation for '{qid}'")

    try:
        result = bench_fn(scenario)
        # If the scenario expects completion tracking but the backend only measures
        # enqueue (no workers running), annotate the result so the report is accurate.
        if (scenario.get("wait") and "completed" not in result
                and not result.get("skipped") and "error" not in result):
            result["notes"] = "enqueue-only"
        return result
    except Exception as exc:
        return {"error": str(exc), "skipped": False}


# ── Report generation ────────────────────────────────────────────────

def fmt_tps(tps):
    return f"{tps:,.0f}" if tps else "N/A"


def fmt_dur(s):
    if s is None:
        return "N/A"
    if s < 1:
        return f"{s * 1000:.1f} ms"
    return f"{s:.2f} s"


def generate_report(all_results: dict, queues_run: list) -> str:
    lines = [
        "# Task Queue Benchmark Results\n",
        "- **Runner**: `ubuntu-latest` (GitHub Actions)",
        "- **Redis**: service container (localhost:6379)",
        "- **PostgreSQL**: service container (localhost:5432) — used by Procrastinate\n",
        "---\n",
        "## Package Overview\n",
        "| # | Package | Category | Broker | Notes |",
        "|---|---------|----------|--------|-------|",
    ]
    for i, q in enumerate(QUEUES, 1):
        lines.append(
            f'| {i} | **{q["name"]}** | {q["category"]} | `{q["broker"]}` | {q["description"]} |'
        )
    lines.append("")

    for sc in SCENARIOS:
        lines += [
            f'## {sc["label"]}',
            f'> {sc["desc"]}\n',
            "| Package | Category | Tasks/s | Elapsed | Completed | Notes |",
            "|---------|----------|--------:|--------:|----------:|-------|",
        ]
        for q in queues_run:
            r = all_results.get((q["id"], sc["name"]), {})
            if r.get("skipped"):
                tps = "—"
                elapsed = "—"
                completed = "—"
                notes = r.get("reason", "skipped")
            elif "error" in r:
                tps = "ERR"
                elapsed = "—"
                completed = "—"
                notes = r["error"][:60]
            else:
                tps = fmt_tps(r.get("tasks_per_sec"))
                elapsed = fmt_dur(r.get("elapsed_s"))
                completed = str(r["completed"]) if "completed" in r else "—"
                notes = r.get("notes", "")
            lines.append(
                f'| {q["name"]} | {q["category"]} | {tps} | {elapsed} | {completed} | {notes} |'
            )
        lines.append("")

    # ── Footnotes ────────────────────────────────────────────────────
    _SUPERSCRIPTS = "¹²³⁴⁵⁶⁷⁸⁹"
    footnotes = [q.get("note") for q in QUEUES if q.get("note")]
    if footnotes:
        lines.append("---\n")
        for idx, note in enumerate(footnotes):
            marker = _SUPERSCRIPTS[idx] if idx < len(_SUPERSCRIPTS) else f"[{idx + 1}]"
            lines.append(f"{marker} {note}\n")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Task Queue Benchmark Suite")
    parser.add_argument(
        "--queues", nargs="*",
        help="Queue IDs to benchmark (default: all). E.g. --queues celery rq huey",
    )
    parser.add_argument(
        "--scenarios", nargs="*",
        help="Scenario names to run (default: all). E.g. --scenarios enqueue-burst",
    )
    args = parser.parse_args()

    queues_to_run = QUEUES
    if args.queues:
        queues_to_run = [q for q in QUEUES if q["id"] in args.queues]

    scenarios_to_run = SCENARIOS
    if args.scenarios:
        scenarios_to_run = [s for s in SCENARIOS if s["name"] in args.scenarios]

    results_dir = PROJECT_ROOT / "benchmarks" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    all_results: dict = {}

    for queue in queues_to_run:
        print(f'\n{"=" * 60}')
        print(f'  {queue["name"]}  ({queue["category"]})')
        print(f'{"=" * 60}')

        for scenario in scenarios_to_run:
            print(f'  > {scenario["label"]} ... ', end="", flush=True)
            result = run_queue_scenario(queue, scenario)
            all_results[(queue["id"], scenario["name"])] = result

            if result.get("skipped"):
                print(f'SKIP — {result.get("reason", "")}')
            elif "error" in result:
                print(f'ERROR — {result["error"]}')
            else:
                tps = fmt_tps(result.get("tasks_per_sec"))
                elapsed = fmt_dur(result.get("elapsed_s"))
                completed = result["completed"] if "completed" in result else "—"
                notes = f'  [{result["notes"]}]' if result.get("notes") else ""
                print(f'{tps} tasks/s  elapsed={elapsed}  completed={completed}{notes}')

    # ── Save reports ──────────────────────────────────────────────────
    report = generate_report(all_results, queues_to_run)
    (results_dir / "task_queue_summary.md").write_text(report)

    json_data = {f"{k[0]}|{k[1]}": v for k, v in all_results.items()}
    (results_dir / "task_queue_raw.json").write_text(
        json.dumps(json_data, indent=2, default=str)
    )

    print(f"\nReport saved to benchmarks/results/task_queue_summary.md")
    print("\n" + report)


if __name__ == "__main__":
    main()
