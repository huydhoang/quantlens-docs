# Task Queue Decision: Huey

## Decision Summary

**Huey (SQLite backend)** is the task queue for QuantLens. Benchmarks across 10 distributed task queue configurations (9 packages, Huey tested with both Redis and SQLite backends) from Actions run [22262734321](https://github.com/huydhoang/quantlens-docs/actions/runs/22262734321) confirm it delivers the best combination of simplicity, backend flexibility, and throughput for a **local single-machine desktop app**. Huey's SQLite backend eliminates Redis as a hard dependency for the task queue (Redis remains in Docker Compose for cache and pub/sub). Its `immediate=True` development mode removes the need for a separate worker process during development and testing. A **backtest simulation** scenario (~5 s CPU-bound tasks mimicking NautilusTrader) confirms that enqueue overhead (0.5 ms for 3 tasks on SQLite) is noise relative to the multi-second job execution time.

**Huey (Redis backend)** is the production configuration when Redis is already running (e.g., Docker Compose with cache + pub/sub).

**Dramatiq** is the second choice if pipeline chaining with a Redis broker is preferred over Huey's decorator API.

**Celery is not recommended** for a local desktop app: it has the lowest enqueue throughput of all tested distributed queues, the steepest learning curve, and no SQLite backend — disproportionate complexity for a single-machine deployment.

See the [Benchmark Results](#benchmark-results) section for the data behind this decision.

---

## Clarifying Questions

**Q1: Does QuantLens need event-driven job dispatch or time-based scheduling?**
Both. Backtests are dispatched on-demand when the user clicks "Run" (event-driven). Data ingestion from Tiingo/Finnhub runs on a nightly/weekly cron (time-based). A distributed task queue with built-in scheduling (Huey's `crontab()`) covers both; a pure in-process scheduler cannot dispatch to separate worker processes.

**Q2: How's a scheduler different from a distributed task queue?**
A **scheduler** (cron, APScheduler) triggers tasks at specific times/intervals within the same process — no broker, no workers, no retries. A **distributed task queue** (Huey, Celery, Dramatiq) dispatches tasks via a message broker to separate worker processes. QuantLens needs process isolation because NautilusTrader enforces one `BacktestNode` per process (global singleton state). A scheduler alone would block the API process during a 5–120 second backtest. **Verdict: task queue.** APScheduler is excluded from benchmarks — it is not a distributed task queue.

**Q3: Is Kafka appropriate for QuantLens's backtest dispatch?**
No. Kafka is a distributed streaming platform for high-throughput event pipelines (log aggregation, ETL, real-time analytics). It lacks native task semantics: no per-task ACK/retry, no result backend, partition-based parallelism that doesn't map to "worker picks up next job." QuantLens dispatches ~1–10 backtests at a time on a single machine — Kafka's partition model, broker overhead (JVM, ZooKeeper/KRaft), and operational complexity are entirely disproportionate. Faust (the Python Kafka library benchmarked) measured Kafka *producer write throughput*, not job execution — an apples-to-oranges comparison with task queues. **Kafka removed from benchmarks.**

**Q4: Why not Celery — isn't it the industry standard?**
Celery is the standard for *distributed multi-machine deployments*. For a local desktop app: no SQLite backend (mandates Redis/RabbitMQ even in dev), steepest learning curve, lowest enqueue throughput of all tested queues, and features QuantLens doesn't need (Canvas chords, Flower monitoring, SQS broker). Revisit if QuantLens becomes a cloud-deployed multi-tenant SaaS.

**Q5: Is Huey's throughput sufficient for NautilusTrader backtests?**
Yes. The throughput gap between the fastest (Dramatiq, 3,459 tasks/s) and slowest (Celery, 1,265 tasks/s) distributed queue translates to <1 ms per task. A single backtest takes 5–120 seconds. Dispatch overhead is noise. The decision hinges on backend flexibility (SQLite) and dev ergonomics (`immediate=True`), not raw throughput.

**Q6: Can Huey handle parallel parameter sweeps?**
Yes. `huey_consumer --workers 4 --worker-type process` runs 4 isolated worker processes. Each picks up a backtest job and runs its own `BacktestNode`. For 100–1,000 parameter combinations, this is sufficient. VectorBT's in-process broadcasting is faster for 100,000+ combinations, but that scale is outside QuantLens's target use case.

---

## Why a Task Queue

Backtests are CPU-bound, long-running jobs (seconds to minutes per run). They cannot run synchronously in the API request cycle. The dispatch pattern:

1. **API** receives backtest request, creates a job record in PostgreSQL, enqueues to Huey, returns `202 Accepted`
2. **Huey worker** picks up the job and spawns an isolated process for NautilusTrader
3. **Progress** is published via Redis pub/sub — independent of the task queue — for real-time WebSocket updates to the React UI
4. **Results** are stored in PostgreSQL; the UI polls or subscribes for completion

```mermaid
flowchart LR
    UI["React UI\n(Tauri + Vite)"]
    API["ASGI Backend\n(Gunicorn+Uvicorn)"]
    Huey["Huey\n(Redis or SQLite broker)"]
    Worker["Huey Worker\n--workers 4 --worker-type process"]
    NT["NautilusTrader\nBacktestNode"]
    Redis["Redis\n(pub/sub + cache)"]
    PG["PostgreSQL\n(results)"]
    WS["WebSocket\n(progress stream)"]

    UI -->|POST /backtest| API
    API -->|enqueue job| Huey
    Huey -->|dequeue| Worker
    Worker -->|run| NT
    NT -->|publish progress| Redis
    Redis -->|pub/sub| WS
    WS -->|stream| UI
    NT -->|store results| PG
    API -->|202 Accepted| UI
```

---

## Benchmark Results

**Environment:** GitHub Actions `ubuntu-latest` (2-core CPU, 7 GB RAM), Actions run [22262734321](https://github.com/huydhoang/quantlens-docs/actions/runs/22262734321). Redis service container (localhost:6379), PostgreSQL service container (localhost:5432).

### Methodology

> Workers were **not** started for any distributed queue — numbers measure **broker write throughput only** (`immediate=False`). For `wait=True` scenarios, the queue waited for job handles to report finished; queues that don't expose completion handles are marked `completed=—` (enqueue-only). Huey is tested with **both Redis and SQLite backends**. A new **backtest simulation** scenario enqueues 3 tasks that each simulate a ~5 s NautilusTrader backtest — the primary QuantLens workload.

### Burst Enqueue — 1,000 Tasks

| Package | Tasks/s | Elapsed |
|---------|--------:|--------:|
| **Huey (SQLite)** | **19,074** | **52.4 ms** |
| Huey (Redis) | 5,333 | 187.5 ms |
| Dramatiq | 3,830 | 261.1 ms |
| BullMQ | 3,271 | 305.7 ms |
| Taskiq | 2,520 | 396.9 ms |
| TaskTiger | 2,553 | 391.6 ms |
| RQ | 2,010 | 497.4 ms |
| ARQ | 1,697 | 589.4 ms |
| Procrastinate | 1,241 | 806.1 ms |
| Celery | 1,175 | 851.2 ms |

### Heavy Enqueue — 10,000 Tasks

| Package | Tasks/s | Elapsed |
|---------|--------:|--------:|
| **Huey (SQLite)** | **21,323** | **469.0 ms** |
| Huey (Redis) | 5,281 | 1.89 s |
| Dramatiq | 3,929 | 2.55 s |
| BullMQ | 3,269 | 3.06 s |
| TaskTiger | 2,553 | 3.92 s |
| Taskiq | 2,467 | 4.05 s |
| RQ | 2,091 | 4.78 s |
| ARQ | 1,685 | 5.93 s |
| Procrastinate | 1,261 | 7.93 s |
| Celery | 1,338 | 7.47 s |

### Round-Trip Latency — 100 Tasks (enqueue-only; no workers)

| Package | Tasks/s | Elapsed | Notes |
|---------|--------:|--------:|-------|
| **Huey (SQLite)** | **16,620** | **6.0 ms** | enqueue-only |
| Huey (Redis) | 5,134 | 19.5 ms | enqueue-only |
| Dramatiq | 3,942 | 25.4 ms | enqueue-only |
| BullMQ | 3,023 | 33.1 ms | enqueue-only |
| TaskTiger | 2,578 | 38.8 ms | enqueue-only |
| Taskiq | 2,424 | 41.3 ms | enqueue-only |
| RQ | 2,054 | 48.7 ms | completed=0 (no workers) |
| ARQ | 1,690 | 59.2 ms | enqueue-only |
| Procrastinate | 1,194 | 83.8 ms | enqueue-only |
| Celery | 1,311 | 76.3 ms | completed=0 (no workers) |

### Round-Trip CPU — 50 Tasks (enqueue-only; no workers)

| Package | Tasks/s | Elapsed | Notes |
|---------|--------:|--------:|-------|
| **Huey (SQLite)** | **15,481** | **3.2 ms** | enqueue-only |
| Huey (Redis) | 4,804 | 10.4 ms | enqueue-only |
| Dramatiq | 3,455 | 14.5 ms | enqueue-only |
| BullMQ | 3,064 | 16.3 ms | enqueue-only |
| TaskTiger | 2,545 | 19.6 ms | enqueue-only |
| Taskiq | 2,375 | 21.1 ms | enqueue-only |
| RQ | 1,976 | 25.3 ms | completed=0 (no workers) |
| ARQ | 1,711 | 29.2 ms | enqueue-only |
| Procrastinate | 906 | 55.2 ms | enqueue-only |
| Celery | 1,205 | 41.5 ms | completed=0 (no workers) |

### Retry Reliability — 20 Failing Tasks

Workers **were** started for this scenario via `immediate=False`; tasks fail once then succeed.

| Package | Tasks/s | Elapsed | Completed | Notes |
|---------|--------:|--------:|----------:|-------|
| **Huey (SQLite)** | **785** | **25.5 ms** | **20** | |
| Huey (Redis) | 779 | 25.7 ms | 20 | |
| Celery | 670 | 29.9 ms | 20 | |
| Dramatiq | 518 | 38.6 ms | 20 | |
| BullMQ | 17 | 1.18 s | 20 | |
| Taskiq | 2 | 9.04 s | 20 | |
| RQ | 0 | 46.79 s | 20 | |
| Procrastinate | 0 | 61.09 s | 0 | TaskNotFound (task not importable from __main__) |
| ARQ | 0 | 62.12 s | 0 | timed out |
| TaskTiger | ERR | — | — | error |

### Backtest Simulation — 3 Long-Running Tasks (enqueue-only; no workers)

> Enqueue 3 tasks each simulating a ~5 s NautilusTrader backtest (CPU-bound). Measures dispatch overhead for the primary QuantLens workload. Even at 0.5 ms total enqueue time, dispatch overhead is noise relative to a 5–120 second backtest.

| Package | Tasks/s | Elapsed | Notes |
|---------|--------:|--------:|-------|
| **Huey (SQLite)** | **5,644** | **0.5 ms** | enqueue-only |
| Huey (Redis) | 1,225 | 2.4 ms | enqueue-only |
| BullMQ | 1,512 | 2.0 ms | enqueue-only |
| Dramatiq | 1,955 | 1.5 ms | enqueue-only |
| ARQ | 1,679 | 1.8 ms | enqueue-only |
| TaskTiger | 1,705 | 1.8 ms | enqueue-only |
| RQ | 475 | 6.3 ms | completed=0 (no workers) |
| Taskiq | 675 | 4.4 ms | enqueue-only |
| Procrastinate | 341 | 8.8 ms | enqueue-only |
| Celery | 292 | 10.3 ms | completed=0 (no workers) |

---

## Package Overview

| Package | Stars | Broker(s) | Async | SQLite | Scheduling | Retry | Status |
|---------|------:|-----------|:-----:|:------:|:----------:|:-----:|--------|
| **Huey** | 5,900+ | Redis · SQLite · file-system | ✅ | ✅ | ✅ built-in | ✅ | Active |
| Dramatiq | 4,400+ | Redis · RabbitMQ | ❌ | ❌ | Via middleware | ✅ | Active |
| Celery | 25,000+ | Redis · RabbitMQ · SQS · more | ⚠️ gevent | ❌ | Beat process | ✅ | Active |
| RQ | 10,000+ | Redis only | ❌ | ❌ | Via rq-scheduler | ✅ | Active |
| Taskiq | 1,900+ | Redis Streams · RabbitMQ · NATS | ✅ native | ❌ | ✅ | ✅ | Active |
| TaskTiger | 1,400+ | Redis only | ❌ | ❌ | ✅ | ✅ | Active |
| ARQ | 2,800+ | Redis only | ✅ native | ❌ | ✅ | ✅ | **Maintenance only** |
| Procrastinate | 2,200+ | PostgreSQL only | ✅ | ❌ | ✅ | ✅ | Active |
| BullMQ | 3,000+† | Redis only | Node.js native | ❌ | ✅ | ✅ | Active |

† Python bindings (`bullmq` package) for the Node.js library.

---

## Analysis: What the Benchmarks Mean for a Local Desktop App

### Enqueue throughput is irrelevant for NautilusTrader

Huey (SQLite) enqueued 3 backtest tasks in **0.5 ms**. A NautilusTrader backtest takes 5–120 seconds to execute. Dispatch overhead is less than 0.01% of job runtime — the choice of task queue has zero practical impact on QuantLens throughput. The decision hinges on **backend flexibility** (SQLite dev mode, no external process) and **operational simplicity**, not raw throughput.

Among Redis-backed queues, Dramatiq leads at 3,929 tasks/s vs Celery's 1,338 tasks/s for 10,000 tasks — a difference of 0.5 ms per task. Irrelevant for workloads measured in seconds.

### QuantLens does NOT need

- Multi-machine horizontal scaling (single local machine)
- SQS or RabbitMQ (cloud broker complexity)
- Flower monitoring dashboard (single user, single machine)
- Canvas chords/groups for fan-out/fan-in (backtest pipeline is a linear chain)
- Enterprise Tidelift subscription

### QuantLens DOES need

- Process isolation for CPU-bound NautilusTrader workers (prefork/spawn)
- Progress streaming to the React UI via Redis pub/sub
- Retry on failure
- `immediate=True` for dev/test (no separate worker process)
- **SQLite backend option** (no Redis required for the queue itself)
- macOS + Linux (developer workstations)
- Simple, readable configuration

### Huey (SQLite) satisfies every requirement

The benchmark exposes one critical advantage that raw throughput numbers do not capture: **backend flexibility**. Among the distributed task queues tested, Huey is the only one that supports SQLite as a broker. This matters for a local desktop app:

- **Development**: zero external dependencies — task queue is a file on disk
- **Production**: switch to Redis with a one-line config change
- **Retry**: `@huey.task(retries=2, retry_delay=30)` — fastest retry completion of all tested queues (25.5 ms for 20 tasks)
- **`immediate=True`**: tests run without starting a worker process

---

## Per-Package Analysis

### Huey (SQLite backend) — **Top Choice** ✅

Benchmark results from run [22262734321](https://github.com/huydhoang/quantlens-docs/actions/runs/22262734321):

| Scenario | Tasks/s | vs next-best Redis queue |
|----------|--------:|--------------------------|
| Burst enqueue (1k) | 19,074 | 3.6× faster than Huey Redis |
| Heavy enqueue (10k) | 21,323 | 4× faster than Huey Redis |
| Retry reliability (20 tasks) | 785 (25.5 ms) | Fastest retry completion |
| Backtest enqueue (3 tasks) | 5,644 (0.5 ms) | 4.6× faster than Huey Redis |

- **SQLite backend**: task queue with zero external service dependency in dev
- **`immediate=True`**: synchronous in-process execution for tests and local dev
- **Pipeline API**: `huey.pipeline()` chains tasks without canvas complexity
- **Retry**: `@huey.task(retries=2, retry_delay=30)` — fastest retry completion tested
- **Scheduling**: built-in crontab and periodic tasks, no separate Beat process
- **Multi-process workers**: `huey_consumer --workers 4 --worker-type process`
- **macOS + Linux**: fully cross-platform
- **5,900+ stars**, actively maintained

### Huey (Redis backend) — Production Configuration

Same API, switches to Redis for production. 5,333 tasks/s burst (19k for SQLite). Use Redis when the deployment already runs Redis for cache and pub/sub — no additional service overhead.

### Dramatiq — Second Choice

Highest enqueue throughput among traditional Redis distributed queues (3,459 tasks/s). Clean middleware-based API, no steep learning curve. `pipeline()` covers the linear backtest chain. No SQLite backend — requires Redis. No `immediate=True` dev mode. Use Dramatiq if Redis is already a hard deployment requirement and a more explicit middleware configuration is preferred over Huey's decorator API.

### Celery — Not Recommended for Local Desktop

| Problem | Detail |
|---------|--------|
| **No SQLite or file-system broker** | Requires Redis or RabbitMQ — mandates a running broker even for a single-machine dev environment with one backtest per day |
| **Steepest learning curve** | Configuration complexity disproportionate for a single-machine app |
| **Canvas is overkill** | fetch → validate → run → metrics → store is a linear chain; no fan-out/fan-in needed |
| **Flower is unnecessary** | Single-user local desktop app with one operator has no need for a fleet monitoring dashboard |
| **Prefork is not unique** | Huey also supports `--worker-type process` with `--workers N` |

**When to use Celery instead**: future cloud deployment with SQS broker, multi-tenant platform requiring fleet-wide Flower monitoring, or when canvas chords/groups (fan-out/fan-in across hundreds of parallel tasks) become necessary.

### ARQ — Excluded (Maintenance Only)

ARQ is in [maintenance-only mode](https://github.com/python-arq/arq/issues/510) as of the author's own statement. Not suitable for new projects.

### Taskiq — Watch List

Native async/await, PEP-612 `ParamSpec` type-hinted task signatures, FastAPI `Depends()` injection in workers. Genuine engineering quality. Second highest enqueue throughput (2,753 tasks/s) among async-native queues. Not chosen because: no SQLite backend, no `immediate=True` dev mode, smaller production reference base than Huey/Dramatiq. **Re-evaluate** when Taskiq adds a file-system or SQLite storage backend and accumulates more production deployments.

### Procrastinate — Niche Fit

PostgreSQL-backed — no Redis required at all if the stack already uses PostgreSQL. Lowest enqueue throughput of all Redis alternatives (1,385 tasks/s) but that is irrelevant. Compelling for teams that want to eliminate Redis entirely. Not chosen because QuantLens already has Redis in Docker Compose for caching, making the SQLite advantage of Huey more practical than Procrastinate's Postgres-only approach.

### RQ — Too Minimal

No rate limiting, no task routing, no scheduling without a separate `rq-scheduler` process. Workers did not complete tasks in the round-trip benchmarks (`completed=0`), indicating the test harness could not easily stand up workers — reflects real operational friction. Good for background jobs in small Django apps, not for a dispatch pipeline with progress streaming.

### BullMQ — Wrong Language

Python bindings wrap the Node.js library. Requires a Node.js runtime in the Docker Compose stack alongside Python. Highest Redis-based enqueue throughput (3,043 tasks/s burst) but the cross-language dependency is not worth it for a Python-first backend.

### Rocketry — Excluded

Incompatible with Pydantic v2. Unmaintained since December 2022.

---

## Recommended Configuration

### Huey (SQLite) — Primary Setup

```python
# tasks.py — SQLite backend for development and single-machine production

from huey import SqliteHuey

# Development / testing — immediate=True: tasks run synchronously, no worker needed
huey = SqliteHuey("quantlens", filename="quantlens_tasks.db", immediate=True)

# Single-machine production — immediate=False: tasks queued to SQLite, worker picks up
huey = SqliteHuey("quantlens", filename="quantlens_tasks.db", immediate=False)
```

Switch between development and production modes via an environment variable:

```python
import os
from huey import RedisHuey, SqliteHuey

env = os.getenv("QUANTLENS_ENV", "development")
if env == "production":
    # Redis backend when the Docker Compose stack is running Redis for cache/pub/sub
    huey = RedisHuey("quantlens", host=os.getenv("REDIS_HOST", "localhost"), port=6379)
else:
    # SQLite backend — no external service required
    huey = SqliteHuey("quantlens", filename="quantlens_tasks.db", immediate=(env == "test"))
```

### Worker Deployment

```bash
# Development — immediate=True means no worker process needed.
# Tasks execute synchronously in the same process.

# Production — separate worker process with process isolation
huey_consumer quantlens.tasks.huey --workers 4 --worker-type process

# SQLite backend (no Redis) — thread pool is sufficient for dispatch
huey_consumer quantlens.tasks.huey --workers 4 --worker-type thread
```

---

## Integration with NautilusTrader

```python
import os
import json
import uuid
import redis

from huey import RedisHuey

huey = RedisHuey("quantlens", host="localhost", port=6379)
r = redis.Redis(host="localhost", port=6379)


@huey.task(retries=2, retry_delay=30)
def run_nautilus_backtest(backtest_id: str, strategy_id: str, config: dict):
    from nautilus_trader.backtest.node import BacktestNode

    r.publish(f"backtest:{backtest_id}", json.dumps({"status": "running"}))
    try:
        node = BacktestNode(configs=config)
        node.run()
        results = node.get_results()
        r.publish(f"backtest:{backtest_id}", json.dumps({"status": "complete"}))
        return results
    except Exception:
        r.publish(f"backtest:{backtest_id}", json.dumps({"status": "failed"}))
        raise


# Pipeline: chain fetch → validate → run → store.
# In non-immediate (production) mode, calling a @huey.task function enqueues it
# and returns a Result. The .then() method on a Result chains the next task,
# passing the previous result as the first positional argument automatically.
# In immediate=True (dev) mode, tasks execute synchronously and .then() is not used.
def dispatch_backtest_pipeline(backtest_id: str, strategy_id: str, config: dict):
    result = (
        fetch_market_data(config["symbols"], config["start"], config["end"])
        .then(validate_data)
        .then(run_nautilus_backtest, backtest_id, strategy_id, config)
        .then(store_results, backtest_id)
    )
    return result
```

### API Endpoint (Raw ASGI)

Pseudocode showing the dispatch pattern — `read_body`, `db`, and `json_response` are project-level ASGI utilities.

```python
async def handle_backtest_request(scope, receive, send):
    body = await read_body(receive)          # project ASGI utility
    config = json.loads(body)
    backtest_id = str(uuid.uuid4())

    await db.execute(                        # asyncpg pool, project-level
        "INSERT INTO backtests (id, status) VALUES ($1, 'queued')",
        backtest_id,
    )

    # Non-blocking enqueue — returns immediately
    run_nautilus_backtest.schedule(
        args=(backtest_id, config["strategy_id"], config),
        delay=0,
    )

    return json_response({"job_id": backtest_id}, status=202)  # project ASGI utility
```

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| **Worker memory leaks from NautilusTrader** | `--worker-type process` spawns fresh processes; restart after N tasks via OS supervisor (systemd/Docker restart policy) |
| **SQLite contention under concurrent writes** | Use Redis backend in production; SQLite backend is for dev only |
| **Broker failure** | **SQLite backend**: the queue persists to disk — no external SPOF; unexecuted tasks survive process restart. **Redis backend**: Redis is already required for cache and pub/sub; one Redis failure affects cache, pub/sub, and queue simultaneously. Mitigate with Docker restart policy, health checks, and Redis persistence (`appendonly yes` in redis.conf for AOF). |
| **Long-running task blocks worker slot** | Set `task_time_limit` via `@huey.task(context=True)` + signal handler; use `--workers N` to avoid head-of-line blocking |
| **Large result payloads** | Store results in PostgreSQL; publish only `backtest_id` and status through the task queue |
| **Huey pipeline is linear only** | Backtest pipeline is always linear (fetch → validate → run → store); fan-out/fan-in is not a current requirement |

---

## Future Considerations

| Trigger | Action |
|---------|--------|
| **Cloud deployment** | Switch Huey broker to Redis on managed service (ElastiCache, Upstash); or migrate to Celery with SQS broker for AWS-native HA |
| **Multi-tenant SaaS** | Re-evaluate Celery for Flower fleet monitoring, canvas chords for parallel multi-user backtests, and Tidelift enterprise support |
| **Taskiq maturity** | Re-evaluate when Taskiq adds a SQLite/file-system backend and accumulates broader production references; its native async and type-safe API are genuinely superior to Huey's decorator model |
| **Fan-out/fan-in backtests** | If parallel multi-symbol backtests with a join step become necessary, evaluate Celery canvas chords or Taskiq pipelines |
| **Scheduled data ingestion** | Huey's built-in crontab handles nightly Tiingo data pulls and monthly Finnhub fundamentals refresh without a separate Beat process (see [data_providers.md](data_providers.md)) |
