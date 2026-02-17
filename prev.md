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