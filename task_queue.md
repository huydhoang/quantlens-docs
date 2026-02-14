# Task Queue Decision: Celery

## Decision Summary

**Celery** is the task queue for the backtesting platform. It provides the reliability, ecosystem breadth, and operational maturity required for distributing long-running NautilusTrader backtest jobs across worker pools. The architecture already uses Redis (cache + hot data) and Python (server language), making Celery with a Redis broker the natural fit — no additional infrastructure required.

---

## Why a Task Queue

Backtests are CPU-intensive, long-running jobs (seconds to minutes per run). They cannot execute synchronously in the API request cycle. The system design (see [system_design.md](system_design.md)) defines a **Task Queue → Worker → NautilusTrader** pipeline:

1. **API** receives backtest request, creates a job record, enqueues to the task queue, and returns `202 Accepted`
2. **Worker** picks up the job, initializes the NautilusTrader engine, streams data, and runs the simulation
3. **Progress** is published back through the queue for real-time WebSocket updates to the UI
4. **Results** are stored in PostgreSQL and the UI is notified of completion

This pattern requires a task queue that handles job distribution, retry logic, progress reporting, and horizontal scaling.

---

## Alternatives Considered

| Feature | Celery | Dramatiq | RQ | Taskiq | FluxQueue |
|---------|--------|----------|----|--------|-----------|
| **First Release** | 2009 | 2017 | 2012 | 2022 | 2025 |
| **GitHub Stars** | 25,000+ | 4,400+ | 10,000+ | 1,900+ | 6 |
| **Core Language** | Python | Python | Python | Python | Rust (Python bindings) |
| **Broker Support** | RabbitMQ, Redis, SQS, and more | RabbitMQ, Redis, SQS | Redis only | Redis, RabbitMQ, NATS, Kafka | Redis only |
| **Result Backends** | Redis, PostgreSQL, S3, 10+ others | Redis, Memcached | Redis | Redis, PostgreSQL | Redis |
| **Priority Queues** | ✅ | ✅ | ✅ | ✅ | Not documented |
| **Rate Limiting** | ✅ | ✅ | ❌ | ✅ | ❌ |
| **Canvas Workflows** | ✅ (chains, chords, groups) | ❌ | ❌ | ❌ | ❌ |
| **Monitoring** | Flower (web UI) | dramatiq-dashboard | rq-dashboard | taskiq-dashboard | None |
| **Async Support** | Limited (gevent/eventlet) | No native async | No | ✅ Native async | ✅ Async and sync |
| **FastAPI Integration** | Mature (via celery + Redis) | Community packages | Community packages | Built-in | None |
| **Enterprise Support** | Tidelift subscription | None | Tidelift subscription | None | None |
| **Platform Support** | Linux, macOS | Linux, macOS | Linux, macOS | Linux, macOS | Linux only |
| **Learning Curve** | Steep | Moderate | Low | Low | Low |

### Why Not Dramatiq

Dramatiq is a well-designed alternative with better defaults than Celery and a cleaner API. However, it lacks **canvas workflows** (chains, chords, groups) — which are valuable for composing multi-step backtest pipelines (e.g., fetch data → validate → run backtest → compute metrics → store results). Dramatiq also has a smaller community and fewer production references at scale.

### Why Not RQ

RQ is the simplest option — minimal configuration, easy to learn. However, it lacks **rate limiting** and **task routing** — both important for managing backtest workloads across multiple worker pools with different concurrency models. RQ is best suited for simple background jobs, not the orchestration this platform requires.

### Why Not Taskiq

Taskiq (first release 2022, ~1,900 GitHub stars) was built specifically to address limitations in Celery and Dramatiq for modern async Python applications. It tackles three problems:

1. **Native async/await**: Celery was designed before `asyncio` existed. Running async tasks in Celery requires workarounds via gevent or eventlet pools. Taskiq runs async functions natively — no monkey-patching, no compatibility layers. For a FastAPI backend (which is inherently async), this eliminates an impedance mismatch.

2. **Type safety and IDE integration**: Taskiq uses PEP-612 `ParamSpec` for full type-hinted task signatures. When you call `my_task.kiq(a=1, b=2)`, your IDE validates argument types and autocompletes parameters. Celery's `.delay()` and `.apply_async()` lose all type information at the call site.

3. **Framework dependency injection**: Taskiq integrates directly with FastAPI's dependency system — workers can reuse the same `Depends()` functions as API routes, sharing database sessions, auth contexts, and configuration without separate wiring.

Despite these genuine advantages, Taskiq lacks **canvas workflows** for composing multi-step pipelines, has **no equivalent to Flower** for production observability, and has not yet been stress-tested at the scale that Celery has endured over 15+ years. For QuantLens, where backtests are CPU-bound (not async I/O-bound) and workflow composition is a core requirement, Celery's maturity outweighs Taskiq's ergonomics. Taskiq is worth re-evaluating if the platform shifts toward a predominantly async workload pattern.

### Why Not FluxQueue

FluxQueue is a Rust-core task queue with Python bindings, created in January 2025 (~6 GitHub stars). Its value proposition is compelling for specific scenarios: a lightweight, resource-efficient alternative to Celery with minimal dependencies, lower memory footprint, and high throughput from its Rust core. It supports both async and sync Python functions and uses Redis as its backend.

However, FluxQueue is not viable for QuantLens at this stage:

- **No production track record**: 6 GitHub stars, no known production deployments, no case studies. Task queues are infrastructure — they must be reliable above all else.
- **Redis-only architecture**: No mention of Redis Sentinel or clustering support. A Redis failure means complete queue loss with no failover path.
- **No monitoring**: No equivalent to Flower, no web dashboard, no observability integration. Running user-submitted backtest jobs without visibility into queue depth, worker health, and failure rates is unacceptable.
- **Linux-only**: macOS support is listed as "coming soon," which excludes developer workstations.
- **No canvas workflows**: No chains, chords, or groups for composing multi-step backtest pipelines.
- **No rate limiting or task routing**: Cannot separate backtest workers from data-fetch workers or throttle data provider API calls.
- **Separate worker installation**: Requires `fluxqueue worker install` to download a pre-built Rust binary, adding deployment complexity compared to `pip install celery`.

FluxQueue's Rust performance advantage is largely irrelevant for QuantLens — the bottleneck is NautilusTrader's backtest execution (seconds to minutes per run), not task dispatch overhead (microseconds). The Rust core addresses a problem we don't have while missing features we need.

**Re-evaluate if**: FluxQueue reaches 1.0, adds monitoring, canvas workflows, and cross-platform support. Its architecture is sound and a mature FluxQueue could be a compelling lightweight alternative.

### Other Notable Alternatives

**arq** (~2,800 GitHub stars) — An async-native Redis task queue by Samuel Colvin (author of Pydantic). Lightweight, clean API, and well-suited for async Python. However, arq is now in **maintenance-only mode** (see [python-arq/arq#510](https://github.com/python-arq/arq/issues/510)), making it unsuitable for new projects that need long-term support.

**Huey** (~5,900 GitHub stars) — A lightweight task queue supporting Redis, SQLite, file-system, and in-memory storage. Huey supports task pipelines, retries, scheduling (crontab), and task locking. It's a strong choice for simpler projects, but lacks the multi-broker support, advanced routing, and enterprise-grade monitoring that QuantLens requires.

---

## Why Celery

### 1. Already Referenced in the Architecture

The existing system design and language decision documents already specify Celery:

- [system_design.md](system_design.md): Backtest Execution Flow diagram shows `Task Queue (BullMQ/Redis)` — Celery fills this role on the Python side
- [python_rust_or_go.md](python_rust_or_go.md): Architecture diagram shows `Redis Queue (BullMQ/Celery)` with a `Celery Worker` pool
- [README.md](README.md): Lists `Celery Workers` under the backend stack

### 2. Canvas Workflows for Backtest Pipelines

Celery's canvas primitives map directly to backtest execution patterns:

```python
from celery import chain, group, chord

# Sequential pipeline: fetch → validate → run → analyze
backtest_pipeline = chain(
    fetch_market_data.s(symbols, start_date, end_date),
    validate_data.s(),
    run_nautilus_backtest.s(strategy_id, config),
    compute_metrics.s(),
    store_results.s(backtest_id),
)

# Parallel multi-symbol data fetch, then run backtest
parallel_fetch = chord(
    group(fetch_symbol_data.s(symbol) for symbol in symbols),
    run_nautilus_backtest.s(strategy_id, config),
)
```

No other Python task queue provides this level of workflow composition out of the box.

### 3. Redis Broker — Zero New Infrastructure

The architecture already requires Redis for caching and hot data storage. Celery with `celery[redis]` uses the same Redis instance as its message broker, adding no operational overhead:

```python
app = Celery("quantlens", broker="redis://localhost:6379/0")
app.conf.result_backend = "redis://localhost:6379/1"
```

### 4. Monitoring with Flower

Flower provides a production-ready web UI for monitoring backtest workers — task progress, worker status, queue depths, and failure rates. This is critical for a platform running user-submitted backtest jobs:

```bash
celery -A quantlens flower --port=5555
```

### 5. Horizontal Scaling

Celery workers scale horizontally by adding more processes or machines. Each worker runs an isolated NautilusTrader engine:

```bash
# Scale to 4 worker processes
celery -A quantlens worker --concurrency=4 --pool=prefork

# Add workers on a second machine
celery -A quantlens worker --hostname=worker2@%h
```

The `prefork` pool is ideal for CPU-bound NautilusTrader backtests — each worker process gets its own Python interpreter and memory space, avoiding GIL contention.

### 6. Enterprise Reliability

- **Automatic retry**: Workers reconnect automatically on broker failure
- **Task acknowledgment**: Jobs are not lost if a worker crashes mid-execution
- **Rate limiting**: Prevents overloading data providers (Tiingo, Alpaca, Finnhub)
- **ETA scheduling**: Schedule backtests for off-peak hours
- **15+ years of production hardening**: Edge cases in distributed systems have been found and fixed

---

## Configuration for QuantLens

### Recommended Setup

```python
from celery import Celery

app = Celery("quantlens")

app.conf.update(
    broker_url="redis://localhost:6379/0",
    result_backend="redis://localhost:6379/1",

    # Prefork for CPU-bound NautilusTrader backtests
    worker_pool="prefork",
    worker_concurrency=4,

    # Long-running backtests need extended timeouts
    task_time_limit=3600,          # Hard limit: 1 hour
    task_soft_time_limit=3000,     # Soft limit: 50 minutes (raises SoftTimeLimitExceeded)

    # Retry on broker connection loss
    broker_connection_retry_on_startup=True,

    # Serialize with JSON for safety (no arbitrary code execution)
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # Rate limiting for data provider tasks
    task_default_rate_limit="60/m",

    # Route backtest tasks to dedicated queue
    task_routes={
        "quantlens.tasks.backtest.*": {"queue": "backtests"},
        "quantlens.tasks.data.*": {"queue": "data_fetch"},
    },
)
```

### Worker Deployment

```bash
# Backtest workers (CPU-bound, prefork pool)
celery -A quantlens worker -Q backtests --concurrency=4 --pool=prefork

# Data fetch workers (I/O-bound, gevent pool)
celery -A quantlens worker -Q data_fetch --concurrency=20 --pool=gevent

# Monitoring
celery -A quantlens flower --port=5555
```

---

## Integration with NautilusTrader

```python
from celery import shared_task
from nautilus_trader.backtest.node import BacktestNode

@shared_task(bind=True, max_retries=2)
def run_nautilus_backtest(self, data, strategy_id, config):
    """Execute a NautilusTrader backtest as a Celery task."""
    try:
        node = BacktestNode(configs=config)
        node.run()
        return node.get_results()
    except Exception as exc:
        self.retry(exc=exc, countdown=30)
```

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| **Celery's steep learning curve** | Start with simple `@shared_task` pattern; adopt canvas workflows incrementally |
| **Worker memory leaks from NautilusTrader** | Set `worker_max_tasks_per_child=50` to restart workers after N tasks |
| **Redis as single point of failure** | Use Redis Sentinel or managed Redis (AWS ElastiCache, Upstash) for HA |
| **Long-running tasks blocking workers** | Separate queues for backtests vs. quick tasks; use `task_time_limit` |
| **Serialization of large results** | Store results in PostgreSQL; pass only IDs through the task queue |

---

## Future Considerations

- **Taskiq re-evaluation**: If the platform moves to a fully async FastAPI architecture, Taskiq's native async support and FastAPI dependency injection may become compelling advantages. Re-evaluate when Taskiq reaches broader adoption and adds canvas-style workflow composition.
- **FluxQueue re-evaluation**: If FluxQueue reaches 1.0 with monitoring, canvas workflows, and cross-platform support, its Rust core could offer meaningful resource savings for high-density worker deployments.
- **SQS broker for cloud deployment**: When deploying to AWS, consider switching from Redis to SQS as the Celery broker for managed scalability (`celery[sqs]`).
- **Celery Beat for scheduled jobs**: Use Celery Beat for recurring tasks like nightly data ingestion from Tiingo and monthly fundamentals refresh from Finnhub (see [data_providers.md](data_providers.md)).
