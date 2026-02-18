Datetime: 2026-02-18 00:00

All 6 files are now fixed and consistent. Here's a summary of what was changed:

---

## Changes Applied Across All 6 Files

### Critical Fixes
| Fix | Files Affected |
|-----|----------------|
| **Broken init** — removed `asyncio.run()` at module level (crashes under Granian) | [granian_extended.py](benchmarks/granian_extended.py) |
| **Broken init** — removed `__main__`-only init; added lazy init on first request | [uvicorn_extended.py](benchmarks/uvicorn_extended.py) |
| **Wrong handler arity** — changed all handlers from `(scope, data, query)` to `(scope, data)` | [uvicorn_extended.py](benchmarks/uvicorn_extended.py) |
| **Variable shadowing** — renamed `config` parameter to `params` in backtest workers | [granian_extended.py](benchmarks/granian_extended.py), [uvicorn_extended.py](benchmarks/uvicorn_extended.py), [gunicorn_uvicorn_extended.py](benchmarks/gunicorn_uvicorn_extended.py) |
| **Non-existent API** — replaced `kernel.data_client` with `trader.cache.bars(bar_type)` | All 6 files |

### Consistency Fixes
| Fix | Files Affected |
|-----|----------------|
| **Route standardization** — unified on `/trading/order` everywhere | [granian_extended.py](benchmarks/granian_extended.py), [fastapi_uvicorn_extended.py](benchmarks/fastapi_uvicorn_extended.py) |
| **Health endpoint** — added `GET /health` to all stacks | 4 files that were missing it |
| **Response schemas** — all endpoints now return the same fields | All 6 files |
| **Pydantic models** — standardized to use string fields with manual enum mapping | [fastapi_uvicorn_extended.py](benchmarks/fastapi_uvicorn_extended.py) |
| **CORS** — removed `allow_credentials=True` with wildcard origins | All 3 FastAPI files |
| **Extra endpoints removed** — removed `/portfolio/nested-clusters`, `/backtest/list`, `DELETE /trading/positions/{id}` | [fastapi_gunicorn_uvicorn_extended.py](benchmarks/fastapi_gunicorn_uvicorn_extended.py) |

### Cleanup
- Removed dead `get_kernel()` dependency from all FastAPI files
- Removed unused imports (`Awaitable`, `Venue`, `BinanceExecClientConfig`, `NestedClustersOptimization`, `uvicorn`, `asdict`)
- Added `BarType` import for correct cache-based data access

### What's Consistent Now

All 6 stacks implement the same **8 endpoints** with identical request/response schemas:

| Endpoint | Method |
|----------|--------|
| `/health` | GET |
| `/portfolio/optimize` | POST |
| `/portfolio/hierarchical` | POST |
| `/marketdata/{instrument_id}` | GET |
| `/trading/order` | POST |
| `/trading/positions` | GET |
| `/backtest/run` | POST |
| `/backtest/results/{strategy_name}` | GET |

The only intentional differences are:
- Multi-worker stacks (gunicorn variants) add `worker_pid` to responses
- FastAPI stacks include WebSocket endpoints (raw ASGI stacks don't)

---

Datetime: 2026-02-18 00:00

Created two files:

**[.github/workflows/extended-server-benchmarks.yml](.github/workflows/extended-server-benchmarks.yml)** — `workflow_dispatch` trigger with configurable `duration`, `workers`, and `stacks` inputs. Installs Python deps (uvicorn, granian, gunicorn, fastapi, skfolio), Go-based `hey` load tester, runs the orchestrator, posts summary to Job Summary, and uploads raw results as artifacts.

**[benchmarks/run_extended_benchmarks.py](benchmarks/run_extended_benchmarks.py)** — Orchestrator that benchmarks all 6 stacks sequentially on the same machine for fair comparison. Key design:

**NautilusTrader mocking** — A meta-path import hook is injected into each server subprocess via `python -c`. It intercepts all `nautilus_trader.*` imports with lightweight stubs that support `__call__`, `__await__`, `__iter__`, `__bool__`, `__float__`, etc. — so every code path survives. `skfolio` runs for real, providing genuine CPU-bound work.

**7 scenarios designed to reveal stack differences:**

| Scenario | What it reveals |
|---|---|
| `GET /health` at c=50 | Steady-state request handling overhead |
| `GET /health` at c=200 | Max throughput ceiling |
| `POST /portfolio/optimize` at c=10 | CPU-bound work under moderate load |
| `POST /portfolio/optimize` at c=50 | CPU contention + GIL pressure |
| `POST /portfolio/hierarchical` at c=10 | Different compute profile (clustering) |
| Burst: 5000 × `/health` at c=200 | Connection handling under spike |
| Burst: 100 × `/portfolio/optimize` at c=50 | Queuing under CPU burst |

**Output** — Generates a Markdown comparison table (Req/s, Avg, P50, P90, P99, Errors) per scenario + memory usage (idle vs after load), saved to `benchmarks/results/summary.md` and posted to the GitHub Actions Step Summary. Raw `hey` output and JSON results are uploaded as artifacts.

---

Datetime: 2026-02-18 00:00

Here's the analysis of each server stack against their respective documentation:

---

## 1. Uvicorn (standalone) — **Missing key flags**

**Current:**
```
uvicorn ... --host 0.0.0.0 --port 8000 --workers 2 --log-level warning
```

**Issues:**
- **`--loop` / `--http` not specified.** Defaults to `auto`, which *should* pick `uvloop` + `httptools` when installed (they are, via `uvicorn[standard]`). Per docs: *"uvloop provides greater performance"* and *"httptools provides greater performance."* The `auto` default is fine here since the `[standard]` extra is installed, but explicitly setting `--loop uvloop --http httptools` would guarantee the fast path and make the benchmark intent clear.
- **`--no-access-log`** could be added to avoid any access-log overhead (though `--log-level warning` already suppresses most of it, access log is a separate codepath).
- **`--backlog`** defaults to 2048, which is fine for 200 concurrent connections.

**Verdict:** Mostly OK — `auto` picks the fast implementations. Consider `--no-access-log` for a tiny gain.

---

## 2. Granian — **Good, minor suggestions**

**Current:**
```
granian --interface asgi --host 0.0.0.0 --port 8000 --workers 2 --log-level warning ...
```

**Issues:**
- **`--no-ws`**: Since the benchmark only tests HTTP (the `/json` endpoint), disabling websocket handling with `--no-ws` removes unnecessary overhead.
- **`--http 1`**: The benchmark uses `hey` which is HTTP/1.1 only. Pinning `--http 1` avoids any HTTP/2 negotiation overhead (default `auto` supports both).
- **Threading/backpressure**: Granian docs say *"the default number of runtime threads... is fine for the vast majority of applications"* and *"on asynchronous protocols, the default value for backpressure should work fine."* No change needed here.
- **`--loop`**: Defaults to `auto`, which on Linux will pick uvloop if available (Granian bundles its own loop selection). Fine as-is.

**Verdict:** Fine. Optional: `--no-ws --http 1`.

---

## 3. Gunicorn + Uvicorn Workers — **Several issues**

**Current:**
```
gunicorn benchmarks.fastapi_app:app -k uvicorn.workers.UvicornWorker --workers 2 --bind 0.0.0.0:8000 --log-level warning
```

**Issues found:**

### a) **Deprecated worker class** (Critical)
The uvicorn docs explicitly state:
> *Warning: The `uvicorn.workers` module is deprecated and will be removed in a future release. You should use the `uvicorn-worker` package instead.*

However, there's a bigger option: **Gunicorn 25.x now has a native ASGI worker** (`--worker-class asgi`). If the purpose is to benchmark "gunicorn managing ASGI workers," the native worker should be tested too (or instead). But if the explicit intent is benchmarking the classic `gunicorn+uvicorn` combo, then switch to `uvicorn-worker`:
```
pip install uvicorn-worker
gunicorn ... -k uvicorn_worker.UvicornWorker
```

### b) **`--keep-alive` not set** (Important for throughput)
Gunicorn defaults `keepalive` to **2 seconds**. With 200 concurrent connections running a 300-second benchmark, many connections will be torn down and re-established. The docs say:
> *"Generally set in the 1-5 seconds range for servers with direct connection to the client."*

For a max-throughput benchmark with a local load generator, `--keep-alive 5` (or higher) would reduce connection churn and better reflect real throughput. Compare: uvicorn standalone defaults to 5s, so the gunicorn variants are at a keepalive disadvantage.

### c) **`--access-logfile /dev/null`** or no access log
By default gunicorn's access log is `None` (disabled), so this is fine.

### d) **`--worker-tmp-dir /dev/shm`** (Important on Linux)
The docs warn:
> *"The current heartbeat system involves calling `os.fchmod` on temporary file handlers and may block a worker for arbitrary time if the directory is on a disk-backed filesystem."*

On CI (Ubuntu), `/tmp` may be disk-backed. Using `--worker-tmp-dir /dev/shm` avoids fchmod blocking on disk I/O and is a standard production optimization.

### e) **UvicornWorker uses uvloop + httptools by default**
Per uvicorn docs: *"The `UvicornWorker` implementation uses the `uvloop` and `httptools` implementations."* So the fast HTTP/event-loop path is already active. This is fine.

### f) **`--backlog`** defaults to 2048, fine.

---

## Summary Table

| Stack | Optimized? | Missing/Recommended Flags |
|---|---|---|
| **uvicorn (standalone)** | ~95% | `--no-access-log` (minor); `--loop uvloop --http httptools` (explicit, but `auto` works) |
| **granian** | ~90% | `--no-ws` (removes unused WS overhead), `--http 1` (matches `hey` client) |
| **gunicorn + uvicorn workers** | ~70% | **`-k uvicorn.workers.UvicornWorker` is deprecated**; add `--keep-alive 5` to match uvicorn's default; add `--worker-tmp-dir /dev/shm` to avoid heartbeat disk I/O |

The **gunicorn + uvicorn workers variants** are the least optimized. The most impactful changes for fair comparison would be:
1. **`--keep-alive 5`** — matches uvicorn's default, avoids unfair connection-churn penalty
2. **`--worker-tmp-dir /dev/shm`** — avoids heartbeat I/O blocking
3. Either switch to `uvicorn-worker` package or consider testing gunicorn's built-in `--worker-class asgi` (with `--asgi-loop uvloop`)

Want me to apply these optimizations to the workflow file?

---

Datetime: 2026-02-18 00:00

All optimizations applied:

| Change | Stacks affected |
|---|---|
| `--no-access-log` | Both uvicorn variants |
| `--no-ws --http 1` | Both granian variants |
| `-k uvicorn_worker.UvicornWorker` (non-deprecated) | Both gunicorn variants |
| `--keep-alive 5` (matches uvicorn's default) | Both gunicorn variants |
| `--worker-tmp-dir /dev/shm` (avoids heartbeat disk I/O) | Both gunicorn variants |
| `pip install ... uvicorn-worker` | Install step |

---

Datetime: 2026-02-18 00:00

Excellent — now I have the full picture. Here's the analysis:

---

## File Naming

**`asgi_app.py` is the right name.** It's a raw/vanilla ASGI application — no framework. TechEmpower themselves call their equivalent `app.py` within the "uvicorn" folder (the server, not a framework). The name `asgi_app` makes the contrast with `fastapi_app` clear: one is the framework, the other is the bare protocol. `vanilla_app` would be vague (vanilla what?). Keep `asgi_app.py`.

---

## Fairness Problems in Current Apps

Looking at what TechEmpower does vs. your current code:

### 1. Response body is not equivalent across endpoints

Your `/json` endpoint returns `list(range(100))` — that's **700+ bytes** of JSON. TechEmpower's JSON test returns `{"message":"Hello, World!"}` — **28 bytes**. The large payload means you're benchmarking JSON serialization throughput (where `orjson` vs `json` matters), not server overhead. This favors FastAPI (which uses `orjson`/`ujson` optionally) differently than raw ASGI (which uses stdlib `json`).

**Issue in your code:** `fastapi_app.py` uses `JSONResponse` (which uses stdlib `json` internally unless configured), while `asgi_app.py` uses `json.dumps()` directly. They happen to both use stdlib `json`, so this is accidentally fair — but the 100-element list magnifies serialization cost and masks server-level differences.

### 2. The `/json` body is recomputed identically every time

`list(range(100))` produces the same thing on every request. TechEmpower explicitly requires: *"the computational effort to serialize an object to JSON must occur within the scope of handling each request."* Your code does this (rebuilds the list each call), but the fact that it's static data means a smart framework could theoretically cache it.

### 3. No `Content-Length` header in FastAPI variant

`asgi_app.py` correctly sets `Content-Length`. FastAPI's `JSONResponse` also sets it. This is fine — both are fair.

### 4. Missing: `orjson` / `ujson` parity

TechEmpower's granian uses **`orjson`** while their uvicorn uses **`ujson`**. Your apps both use **stdlib `json`** — which is actually *more fair* for a server-level comparison since serialization is identical. Keep this.

---

## Recommendations for Better Real-World Simulation

The current apps test only the absolute floor (trivial JSON). To reveal differences that matter in production while staying minimal and fair:

### Add 3 tiers of work to both apps identically:

| Endpoint | What it exercises | Why it matters |
|---|---|---|
| `/json` | Minimal JSON (~28 bytes) | Pure server overhead, routing, keep-alive — granian's advantage |
| `/compute` | Synchronous CPU work (e.g. compute fibonacci or sort random data) | GIL contention, worker model differences |
| `/async-io` | Simulated async I/O (`asyncio.sleep(0.01)`) | Event loop efficiency, concurrency handling — uvicorn's advantage |

### Key fairness rules (from TechEmpower + common sense):

1. **Same JSON serializer** — both apps must use the same `json.dumps` (or both use `orjson`). Currently both use stdlib `json`. Good.
2. **Same response body** — byte-identical responses. Currently true. Good.
3. **Same computational work** — the compute/sleep must be identical code, not framework-dependent.
4. **No caching** — fresh object per request.
5. **Disable all logging** — both already do via server flags.
6. **Pre-built response dicts** — TechEmpower pre-builds the `http.response.start` dicts as module-level constants (see their code). Your `asgi_app.py` doesn't — it rebuilds the headers dict on every request. FastAPI does this internally. This creates unfair overhead in the ASGI app.

### Specific code improvements:

**`asgi_app.py`** — Pre-allocate response-start dicts as module-level constants (like TechEmpower does):
```python
JSON_RESPONSE = {
	"type": "http.response.start",
	"status": 200,
	"headers": [[b"content-type", b"application/json"]],
}
```
Currently your code rebuilds this dict with the `content-length` header per-request, which is more correct HTTP but adds overhead that FastAPI also pays, so it's actually fair. However, TechEmpower omits `Content-Length` in the start dict and lets the framework/server handle it, or computes it once. Either way, keep both apps consistent.

**`fastapi_app.py`** — `JSONResponse` does extra work that raw ASGI doesn't: middleware chain, exception handling, routing overhead. That's the *point* of comparing framework vs raw. No change needed.

---

## Why uvicorn beat granian in query tests but lost in JSON

This aligns perfectly with architecture:

- **JSON serialization** — zero I/O, pure event-loop throughput. Granian's Rust-based HTTP parsing and connection handling wins when the Python work is trivial.
- **Database queries** — actual async I/O. Uvicorn's `uvloop` + `httptools` combination has better-optimized interaction with Python's `asyncio` ecosystem (asyncpg, etc.) because the entire stack speaks the same protocol natively. Granian has to cross the Rust ↔ Python boundary for each async operation.

### What this means for your benchmark:

Your current `/json` endpoint with `list(range(100))` is **too heavy** for a pure server-overhead test (it's 700+ bytes of serialization) but **too light** for a real-world simulation. It sits in an awkward middle.

**Recommendation:** Split into the canonical TechEmpower-style small JSON (`{"message":"Hello, World!"}`) plus a separate endpoint with meaningful async work. This way your benchmark will capture both the scenarios where granian wins AND where uvicorn wins, giving a complete picture.

---

## About gunicorn+uvicorn missing from TechEmpower

TechEmpower's uvicorn entry actually **uses gunicorn as its process manager** (`gunicorn app:main -k uvicorn.workers.UvicornWorker`). So when you see "uvicorn" in TechEmpower results, it IS gunicorn+uvicorn. Their config uses `keepalive = 120` and `workers = cpu_count()`. Your workflow already tests uvicorn standalone (its own multiprocess manager) AND gunicorn+uvicorn separately — which is more informative than what TechEmpower does.

---

Want me to implement the improved apps with these tiered endpoints?

---

## Previous answer

All three files are updated. Here's what changed:

### [benchmarks/asgi_app.py](benchmarks/asgi_app.py)
- `/json` — returns `{"message":"Hello, World!"}` (28 bytes, pre-encoded at module level)
- `/compute` — sorts 5,000 random floats with seeded RNG (deterministic CPU work)
- `/async-io` — `asyncio.sleep(0.01)` simulating 10ms I/O
- `/health` — unchanged
- Pre-allocated `http.response.start` dicts as module-level constants (TechEmpower pattern)

### [benchmarks/fastapi_app.py](benchmarks/fastapi_app.py)
- `/json` — returns `Response(content=_JSON_BODY)` with pre-encoded bytes (byte-identical to ASGI app)
- `/compute` — same seeded sort of 5,000 floats
- `/async-io` — same 10ms sleep
- `/health` — unchanged

### [.github/workflows/server-benchmarks.yml](.github/workflows/server-benchmarks.yml)
Replaced the single 300s `/json` benchmark with **4 scenarios per stack**:

| Scenario | Concurrency | Duration | What it reveals |
|---|---|---|---|
| `/json` | c=200 | 60s | Pure server overhead / max throughput |
| `/compute` | c=50 | 60s | CPU-bound work, GIL contention |
| `/async-io` | c=200 | 60s | Event loop efficiency, async I/O handling |
| `/json` burst | 5000 reqs, c=200 | fixed count | Connection handling under spike |

Summary now includes P50 latency alongside Avg/P99, and results are saved to `results/summary.md`.

