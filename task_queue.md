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

| Feature | Celery | Dramatiq | RQ | Taskiq |
|---------|--------|----------|----|--------|
| **First Release** | 2009 | 2017 | 2012 | 2022 |
| **GitHub Stars** | 25,000+ | 4,400+ | 10,000+ | 1,000+ |
| **Broker Support** | RabbitMQ, Redis, SQS, and more | RabbitMQ, Redis, SQS | Redis only | Redis, RabbitMQ, NATS, Kafka |
| **Result Backends** | Redis, PostgreSQL, S3, 10+ others | Redis, Memcached | Redis | Redis, PostgreSQL |
| **Priority Queues** | ✅ | ✅ | ✅ | ✅ |
| **Rate Limiting** | ✅ | ✅ | ❌ | ✅ |
| **Canvas Workflows** | ✅ (chains, chords, groups) | ❌ | ❌ | ❌ |
| **Monitoring** | Flower (web UI) | dramatiq-dashboard | rq-dashboard | taskiq-dashboard |
| **Async Support** | Limited (gevent/eventlet) | No native async | No | ✅ Native async |
| **FastAPI Integration** | Mature (via celery + Redis) | Community packages | Community packages | Built-in |
| **Enterprise Support** | Tidelift subscription | None | Tidelift subscription | None |
| **Learning Curve** | Steep | Moderate | Low | Low |

### Why Not Dramatiq

Dramatiq is a well-designed alternative with better defaults than Celery and a cleaner API. However, it lacks **canvas workflows** (chains, chords, groups) — which are valuable for composing multi-step backtest pipelines (e.g., fetch data → validate → run backtest → compute metrics → store results). Dramatiq also has a smaller community and fewer production references at scale.

### Why Not RQ

RQ is the simplest option — minimal configuration, easy to learn. However, it lacks **rate limiting** and **task routing** — both important for managing backtest workloads across multiple worker pools with different concurrency models. RQ is best suited for simple background jobs, not the orchestration this platform requires.

### Why Not Taskiq

Taskiq is the newest contender with native async/await support and strong FastAPI integration. It is architecturally modern and type-hinted. However, it is the **least mature** option (first release 2022, ~1,000 GitHub stars). For infrastructure-critical components like task queues, battle-tested reliability outweighs API ergonomics. Taskiq is worth re-evaluating in future phases as it matures.

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

- **Taskiq re-evaluation**: If the platform moves to a fully async FastAPI architecture, Taskiq's native async support may become a compelling advantage. Re-evaluate when Taskiq reaches v1.0+ and broader adoption.
- **SQS broker for cloud deployment**: When deploying to AWS, consider switching from Redis to SQS as the Celery broker for managed scalability (`celery[sqs]`).
- **Celery Beat for scheduled jobs**: Use Celery Beat for recurring tasks like nightly data ingestion from Tiingo and monthly fundamentals refresh from Finnhub (see [data_providers.md](data_providers.md)).
