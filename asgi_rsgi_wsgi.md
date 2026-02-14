# Web Server Interface Decision: ASGI

## Decision Summary

**ASGI** is the web server interface for QuantLens backend services. We will use **FastAPI** as the ASGI framework, with **Granian** as the ASGI server for all environments (development, CI, staging, and production).

---

## Context

QuantLens integrates:

- **NautilusTrader** (Rust core + async tokio networking, REST/WebSocket adapter model)
- **PyPortfolioOpt** (synchronous, CPU-bound portfolio optimization via scipy/cvxpy stack)

This creates a mixed workload profile:

1. I/O-bound, real-time streaming (market data, order updates, P&L)
2. CPU-bound optimization jobs (portfolio construction/risk optimization)

---

## Comparison

| Factor | NautilusTrader | PyPortfolioOpt | Implication |
|--------|---------------|----------------|-------------|
| **Architecture** | Rust core + async tokio networking | Pure Python, CPU-bound optimization | Nautilus aligns with async runtime; optimization needs sync wrapper |
| **Real-time Requirements** | WebSocket streaming, live trading, market data | Batch portfolio optimization | WebSocket support is required |
| **Concurrency Model** | Event-driven, high-frequency capable | Synchronous scipy/cvxpy solvers | Need mixed async + sync execution model |
| **Integration Pattern** | Modular adapters for REST/WebSocket | Library-style function calls | Need both HTTP API and WebSocket endpoints |

---

## Why ASGI

1. **Matches NautilusTrader's async model**  
   NautilusTrader is built around asynchronous networking and event-driven processing. ASGI maps directly to that model.

2. **Supports WebSockets (required)**  
   Live market data, P&L streams, and order status updates require WebSocket endpoints.

3. **Handles mixed sync/async workloads**  
   ASGI servers can run synchronous, CPU-heavy PyPortfolioOpt calls in worker pools without blocking the main event loop.

4. **Strong performance options**  
   ASGI-native servers (Uvicorn, Granian) provide high throughput for I/O-heavy APIs and streaming use cases.

5. **Future-proof protocol support**  
   ASGI is the modern Python interface for long-lived connections and streaming workloads.

---

## Why Not WSGI or RSGI

| Interface | Why it is suboptimal for this stack |
|-----------|-------------------------------------|
| **WSGI** | Request/response only, no native WebSocket support, and blocking model is a poor fit for NautilusTrader streaming requirements |
| **RSGI** | Rust-oriented interface. While supported by Granian, it provides no practical ecosystem advantage for FastAPI/Starlette-based Python services |

---

## Implementation Pattern

Use ASGI endpoints for streaming and REST, and isolate CPU-heavy optimization in executors.

```python
from fastapi import FastAPI, WebSocket
import asyncio
from concurrent.futures import ProcessPoolExecutor

app = FastAPI(title="Trading + Portfolio Optimization API")
executor = ProcessPoolExecutor(max_workers=4)

@app.websocket("/ws/market-data")
async def market_data_stream(websocket: WebSocket):
    await websocket.accept()
    async for tick in nautilus_data_stream():
        await websocket.send_json(tick)

@app.post("/optimize-portfolio")
async def optimize_portfolio(holdings: dict):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(executor, run_optimization_sync, holdings)
```

---

## Server Choice: Granian

**Granian** is the ASGI server for all environments (development, CI, staging, and production).

### Why Granian for All Environments

- **Rust-based server implementation** tuned for high-throughput ASGI workloads, providing a strong fit with a NautilusTrader-centric, performance-oriented backend
- **Hot reload support** via optional reload dependency (see [Granian documentation](https://deepwiki.com/emmett-framework/granian/1-overview)), providing comparable developer experience to Uvicorn during local development
- **Runtime parity across all environments** — using one server eliminates behavioral differences (timeouts, worker defaults, connection handling) and simplifies configuration management
- **Reduced operational complexity** — maintaining a single server configuration reduces maintenance burden and testing surface area

### Trade-offs

The primary trade-off is **Granian's smaller community** compared to Uvicorn. However, for standard ASGI/FastAPI usage, this is a minor concern:

- Granian implements the ASGI 3.0 specification fully
- FastAPI and Starlette applications are server-agnostic
- The Granian project is actively maintained and production-ready

### Operational Considerations in a Hybrid Async + CPU-Bound Architecture

| Area | Consideration / Potential issue | Mitigation |
|------|--------------------------------|------------|
| **Long-lived streams** | WebSocket/SSE behavior can degrade under conservative timeout or keepalive defaults | Explicitly configure keepalive/timeouts and add reconnect + heartbeat logic at clients |
| **CPU-heavy optimization** | PyPortfolioOpt jobs can starve event-loop responsiveness if executed in-process | Always offload optimization to process/thread executors and enforce concurrency limits |
| **Backpressure under load** | Market-data bursts can flood WebSocket consumers | Use bounded queues, drop/coalesce non-critical updates, and emit snapshots on intervals |

### Practical Mitigation Checklist

1. Pin explicit worker, timeout, keepalive, and max-request settings in deployment config.
2. Keep WebSocket handlers non-blocking; move compute work to executors/workers.
3. Add readiness/liveness probes and monitor event-loop lag, p95/p99 latency, and dropped stream events.

