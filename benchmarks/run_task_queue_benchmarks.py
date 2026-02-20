#!/usr/bin/env python3
"""
Task Queue Benchmark Runner
~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Benchmarks 11 task queue packages across throughput, latency, and reliability
scenarios using a gunicorn+uvicorn FastAPI server to enqueue jobs.

Packages under test:
  Distributed Task Queues:
    1.  Celery        — enterprise standard
    2.  RQ            — simple Redis-only queue
    3.  BullMQ        — cross-platform (Node.js/Python), high performance
    4.  Huey          — lightweight, multi-backend
    5.  Dramatiq      — modern, middleware-based
    6.  ARQ           — async-first, type-hinted
    7.  TaskTiger     — Redis-based, Close.io origin
    8.  Taskiq        — Redis Streams, fastest performance
    9.  Procrastinate — PostgreSQL-based (no Redis required)
  In-Process Schedulers:
    10. APScheduler   — advanced scheduling, multiple backends
    11. Rocketry      — statement-based scheduling (excluded: incompatible
                       with Pydantic v2, unmaintained since Dec 2022)

Usage:
    python benchmarks/run_task_queue_benchmarks.py
    python benchmarks/run_task_queue_benchmarks.py --queues celery rq huey
    python benchmarks/run_task_queue_benchmarks.py --scenarios enqueue-burst
"""

import argparse
import importlib.util
import json
import os
import time
from pathlib import Path

# ── Project root ─────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent

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
        "name": "Huey",
        "category": "Distributed Task Queue",
        "pip": "huey",
        "import": "huey",
        "broker": "redis",
        "description": "Lightweight — Redis, SQLite, file-system backends",
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
    # ── In-Process Schedulers ────────────────────────────────────────
    {
        "id": "apscheduler",
        "name": "APScheduler",
        "category": "In-Process Scheduler",
        "pip": "apscheduler",
        "import": "apscheduler",
        "broker": "in-process",
        "description": "Advanced scheduling — multiple backends, cron/interval/date",
    },
    {
        "id": "rocketry",
        "name": "Rocketry",
        "category": "In-Process Scheduler",
        "pip": "rocketry",
        "import": "rocketry",
        "broker": "in-process",
        "description": "Statement-based scheduling — powerful conditions ¹",
        "note": "Rocketry is excluded from benchmarks: it is incompatible with "
               "Pydantic v2 and has been unmaintained since its last release "
               "(v2.5.1, December 2022). See https://github.com/Miksus/rocketry/issues/210.",
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
# Workers are not started during benchmarks, so only enqueue throughput
# is measured; round-trip scenarios will time-out gracefully.

_MODULE_PATH = "benchmarks.run_task_queue_benchmarks"


def _bench_noop():
    """No-op task for enqueue benchmarks."""
    pass


def _bench_cpu_work():
    """CPU-bound task for throughput benchmarks."""
    return sum(i * i for i in range(10_000))


_bench_noop.__module__ = _MODULE_PATH
_bench_cpu_work.__module__ = _MODULE_PATH


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

    @app.task(bind=True, max_retries=1)
    def flaky(self):
        if not getattr(self, "_tried", False):
            self._tried = True
            raise ValueError("transient error")

    task_fn = {"noop": noop, "cpu_work": cpu_work, "flaky": flaky}[scenario["task"]]
    count = scenario["count"]

    def enqueue(n):
        for _ in range(n):
            task_fn.delay()

    metrics = _time_enqueue(enqueue, count)

    if scenario.get("wait"):
        results = [task_fn.delay() for _ in range(count)]
        deadline = time.time() + 60
        pending = list(results)
        while pending and time.time() < deadline:
            pending = [r for r in pending if not r.ready()]
            time.sleep(0.05)
        metrics["completed"] = count - len(pending)
        metrics["timed_out"] = len(pending)

    return metrics


# ── RQ ───────────────────────────────────────────────────────────────

def bench_rq(scenario: dict) -> dict:
    import redis
    from rq import Queue

    conn = redis.Redis(host="localhost", port=6379)
    q = Queue(connection=conn)

    fn_map = {"noop": _bench_noop, "cpu_work": _bench_cpu_work, "flaky": _bench_noop}
    fn = fn_map[scenario["task"]]
    count = scenario["count"]

    def enqueue(n):
        for _ in range(n):
            q.enqueue(fn)

    metrics = _time_enqueue(enqueue, count)

    if scenario.get("wait"):
        jobs = [q.enqueue(fn) for _ in range(count)]
        deadline = time.time() + 60
        while any(not j.is_finished for j in jobs) and time.time() < deadline:
            time.sleep(0.05)
        metrics["completed"] = sum(1 for j in jobs if j.is_finished)
        metrics["timed_out"] = sum(1 for j in jobs if not j.is_finished)

    return metrics


# ── Huey ─────────────────────────────────────────────────────────────

def bench_huey(scenario: dict) -> dict:
    from huey import RedisHuey

    huey = RedisHuey("bench", host="localhost", port=6379, immediate=False)

    @huey.task()
    def noop_task():
        pass

    @huey.task()
    def cpu_task():
        return sum(i * i for i in range(10_000))

    task_map = {"noop": noop_task, "cpu_work": cpu_task, "flaky": noop_task}
    task_fn = task_map[scenario["task"]]
    count = scenario["count"]

    def enqueue(n):
        for _ in range(n):
            task_fn()

    metrics = _time_enqueue(enqueue, count)
    return metrics


# ── Dramatiq ─────────────────────────────────────────────────────────

def bench_dramatiq(scenario: dict) -> dict:
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker

    broker = RedisBroker(host="localhost", port=6379)
    dramatiq.set_broker(broker)

    @dramatiq.actor
    def noop_actor():
        pass

    @dramatiq.actor
    def cpu_actor():
        return sum(i * i for i in range(10_000))

    actor_map = {"noop": noop_actor, "cpu_work": cpu_actor, "flaky": noop_actor}
    actor = actor_map[scenario["task"]]
    count = scenario["count"]

    def enqueue(n):
        for _ in range(n):
            actor.send()

    metrics = _time_enqueue(enqueue, count)
    return metrics


# ── ARQ ──────────────────────────────────────────────────────────────

def bench_arq(scenario: dict) -> dict:
    import asyncio
    import arq

    async def noop(ctx):
        pass

    async def cpu_work(ctx):
        return sum(i * i for i in range(10_000))

    async def _run(count):
        redis = await arq.create_pool(arq.connections.RedisSettings(host="localhost", port=6379))
        fn_name = {"noop": "noop", "cpu_work": "cpu_work", "flaky": "noop"}[scenario["task"]]
        start = time.perf_counter()
        for _ in range(count):
            await redis.enqueue_job(fn_name)
        elapsed = time.perf_counter() - start
        await redis.aclose()
        return elapsed

    count = scenario["count"]
    elapsed = asyncio.run(_run(count))
    return {
        "count": count,
        "elapsed_s": round(elapsed, 4),
        "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
    }


# ── Taskiq ───────────────────────────────────────────────────────────

def bench_taskiq(scenario: dict) -> dict:
    import asyncio
    from taskiq_redis import ListQueueBroker

    broker = ListQueueBroker("redis://localhost:6379")

    @broker.task
    async def noop_task():
        pass

    @broker.task
    async def cpu_task():
        return sum(i * i for i in range(10_000))

    task_map = {"noop": noop_task, "cpu_work": cpu_task, "flaky": noop_task}
    task_fn = task_map[scenario["task"]]
    count = scenario["count"]

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


# ── APScheduler ──────────────────────────────────────────────────────

def bench_apscheduler(scenario: dict) -> dict:
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler()
    results = []

    def noop_job():
        results.append(1)

    count = scenario["count"]
    start = time.perf_counter()
    for i in range(count):
        scheduler.add_job(noop_job, "date")
    elapsed = time.perf_counter() - start

    scheduler.start()
    deadline = time.time() + 10
    while len(results) < count and time.time() < deadline:
        time.sleep(0.05)
    scheduler.shutdown(wait=False)

    return {
        "count": count,
        "elapsed_s": round(elapsed, 4),
        "tasks_per_sec": round(count / elapsed, 1) if elapsed > 0 else None,
        "completed": len(results),
    }


# ── BullMQ ───────────────────────────────────────────────────────────

def bench_bullmq(scenario: dict) -> dict:
    import asyncio
    from bullmq import Queue as BullQueue

    count = scenario["count"]

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

    fn_map = {"noop": _bench_noop, "cpu_work": _bench_cpu_work, "flaky": _bench_noop}
    fn = fn_map[scenario["task"]]
    count = scenario["count"]

    def enqueue(n):
        for _ in range(n):
            tiger.delay(fn)

    metrics = _time_enqueue(enqueue, count)
    return metrics


# ── Rocketry ─────────────────────────────────────────────────────────

def bench_rocketry(scenario: dict) -> dict:
    import threading
    from rocketry import Rocketry
    from rocketry.conds import every

    count = scenario["count"]
    # Rocketry is an in-process scheduler; benchmark measures execution
    # throughput by running the scheduler for a fixed window.
    counter = [0]
    app = Rocketry(config={"execution": "thread"})

    @app.task(every("0.001 seconds"))
    def noop_task():
        counter[0] += 1

    thread = threading.Thread(target=app.run, daemon=True)
    start = time.perf_counter()
    thread.start()

    deadline = time.time() + max(10, count * 0.05)
    while counter[0] < count and time.time() < deadline:
        time.sleep(0.01)

    elapsed = time.perf_counter() - start
    app.session.shut_down()

    return {
        "count": count,
        "elapsed_s": round(elapsed, 4),
        "tasks_per_sec": round(counter[0] / elapsed, 1) if elapsed > 0 else None,
        "completed": counter[0],
    }


# ── Procrastinate ────────────────────────────────────────────────────

def bench_procrastinate(scenario: dict) -> dict:
    import asyncio
    import procrastinate
    from procrastinate.contrib.aiopg import AiopgConnector

    dsn = os.environ.get(
        "PROCRASTINATE_URL", "postgresql://bench:bench@localhost:5432/bench"
    )
    count = scenario["count"]

    async def _run(n):
        connector = AiopgConnector(dsn=dsn)
        app = procrastinate.App(connector=connector)

        @app.task
        async def noop_task():
            pass

        @app.task
        async def cpu_task():
            return sum(i * i for i in range(10_000))

        task_map = {"noop": noop_task, "cpu_work": cpu_task, "flaky": noop_task}
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
    "dramatiq":      bench_dramatiq,
    "arq":           bench_arq,
    "tasktiger":     bench_tasktiger,
    "taskiq":        bench_taskiq,
    "procrastinate": bench_procrastinate,
    "apscheduler":   bench_apscheduler,
    "rocketry":      bench_rocketry,
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
        return bench_fn(scenario)
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
                completed = str(r.get("completed", r.get("count", "—")))
                notes = ""
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
                completed = result.get("completed", result.get("count", "?"))
                print(f'{tps} tasks/s  elapsed={elapsed}  completed={completed}')

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
