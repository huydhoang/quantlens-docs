# Web Server Interface Decision: ASGI

## Decision Summary

**ASGI** is the web server interface for QuantLens backend services. We will use **FastAPI** as the ASGI framework, with **Uvicorn** for local development and **Granian** for production.

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

## Deployment Recommendation

- **Development:** `uvicorn` (debugging ergonomics, reload support)
- **Production:** `granian` (Rust implementation, high-performance ASGI serving)

---

## Final Decision

Adopt **ASGI** for QuantLens backend services to align with NautilusTrader's async design, support required WebSocket workflows, and safely integrate CPU-bound PyPortfolioOpt workloads.
