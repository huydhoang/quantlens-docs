Here's your comprehensive guide to setting up and optimizing **Uvicorn** and **Granian** for maximum performance across all 4 variants. Based on [benchmarks](https://github.com/emmett-framework/granian/blob/master/benchmarks/vs.md) and best practices, Granian generally outperforms Uvicorn in raw throughput, while Uvicorn offers broader compatibility and a more mature ecosystem.

---

## 1. FastAPI on Uvicorn (Production-Optimized)

**Best for:** Compatibility, mature ecosystem, moderate traffic

### Installation
```bash
pip install fastapi uvicorn[standard] gunicorn
```

### Configuration Options

**Option A: Pure Uvicorn (Development/Low Traffic)**
```bash
uvicorn main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers $(nproc) \
  --loop uvloop \
  --http httptools \
  --interface asgi3 \
  --no-access-log \
  --timeout-keep-alive 5 \
  --limit-concurrency 1000 \
  --backlog 2048
```

**Option B: Gunicorn + Uvicorn Workers (Recommended for Production)**

> **Note:** `uvicorn.workers` is deprecated. Install the standalone package:
> `pip install uvicorn-worker`

```bash
gunicorn main:app \
  -w $(nproc) \
  -k uvicorn_worker.UvicornWorker \
  --bind 0.0.0.0:8000 \
  --keep-alive 5 \
  --timeout 120 \
  --graceful-timeout 30 \
  --preload \
  --log-level warning
```

### FastAPI App Optimization
```python
from fastapi import FastAPI
from fastapi.responses import ORJSONResponse

app = FastAPI(
    title="High Performance API",
    openapi_url=None,  # Disable docs in production
    docs_url=None,
    redoc_url=None,
    default_response_class=ORJSONResponse,  # Faster JSON serialization
)

@app.get("/")
async def root():
    return {"message": "Hello World"}
```

**Key Optimizations:**
- **`uvicorn[standard]`**: Installs `httptools` (C-based HTTP parser via Cython bindings) and `uvloop` for significant performance gains
- **Workers**: Set to number of CPU cores (not 2x+1 like WSGI)
- **`--preload`**: Reduces memory usage via copy-on-write (Gunicorn only)
- **`ORJSONResponse`**: Faster JSON serialization than standard `json` module
- **`uvicorn.workers`**: Deprecated — use the `uvicorn-worker` package (`pip install uvicorn-worker`)

---

## 2. FastAPI on Granian (Maximum Performance)

**Best for:** Raw throughput, modern deployments, Rust-backed performance

### Installation
```bash
pip install fastapi granian
```

### Configuration

**Basic High-Performance Setup**
```bash
granian main:app \
  --interface asgi \
  --host 0.0.0.0 \
  --port 8000 \
  --workers $(nproc) \
  --runtime-threads 2 \
  --loop uvloop \
  --http auto \
  --backlog 2048 \
  --no-ws \
  --log-level warning
```

**Advanced Production Configuration**
```bash
granian main:app \
  --interface asgi \
  --host 0.0.0.0 \
  --port 8000 \
  --workers $(nproc) \
  --runtime-threads 2 \
  --loop uvloop \
  --http 1 \
  --backlog 8192 \
  --no-ws \
  --log-level error \
  --no-access-log \
  --http1-keep-alive \
  --http1-pipeline-flush \
  --http1-buffer-size 417792
```

### FastAPI App (Granian-Optimized)
```python
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Granian handles process forking efficiently
    print("Starting up...")
    yield
    # Shutdown
    print("Shutting down...")

app = FastAPI(
    lifespan=lifespan,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)

@app.get("/")
async def root():
    return {"message": "Hello from Granian"}
```

**Key Optimizations:**
- **`--runtime-threads`**: Number of Rust I/O threads per worker; default of 1 is fine for most apps, increase for heavy websocket/HTTP2 use
- **`--http 1`**: Lock to HTTP/1 if HTTP/2 is not needed, avoiding protocol negotiation overhead
- **`--http1-pipeline-flush`**: Aggregates HTTP/1 flushes for better pipelined response support (experimental)
- **`--no-ws`**: Disable WebSocket if not needed to reduce overhead
- **`--no-access-log`**: Access logging is disabled by default; ensure it stays off for max throughput
- **Memory**: Granian has lower memory footprint than Uvicorn+Gunicorn as a single Rust binary

---

## 3. Vanilla ASGI on Uvicorn (Minimal Overhead)

**Best for:** Custom ASGI apps, maximum control, protocol experimentation

### Installation
```bash
pip install uvicorn[standard] starlette
```

### Pure ASGI Application
```python
# asgi_app.py
from starlette.responses import PlainTextResponse, JSONResponse

async def app(scope, receive, send):
    """
    Pure ASGI application - no framework overhead
    """
    if scope["type"] == "http":
        # Minimal routing
        path = scope["path"]
        
        if path == "/":
            response = PlainTextResponse("Hello World")
        elif path == "/json":
            response = JSONResponse({"message": "Hello"})
        else:
            response = PlainTextResponse("Not Found", status_code=404)
        
        await response(scope, receive, send)
    elif scope["type"] == "websocket":
        # Handle WebSocket if needed
        pass
```

### Optimized Uvicorn Command
```bash
uvicorn asgi_app:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers $(nproc) \
  --loop uvloop \
  --http httptools \
  --interface asgi3 \
  --no-access-log \
  --timeout-keep-alive 5 \
  --limit-concurrency 2000 \
  --backlog 4096 \
  --h11-max-incomplete-event-size 16384
```

**Performance Tweaks:**
- **`--interface asgi3`**: Explicitly select ASGI 3.0 spec (avoids auto-detection overhead)
- **`--h11-max-incomplete-event-size`**: Set max buffer for incomplete HTTP events (only applies when using `h11`, not `httptools`)
- **Pure ASGI**: Eliminates FastAPI/Starlette routing overhead

---

## 4. Vanilla ASGI on Granian (Fastest Possible)

**Best for:** Microservices, edge computing, extreme performance requirements

### Installation
```bash
pip install granian
```

### Optimized ASGI Application
```python
# granian_asgi.py
import orjson

async def app(scope, receive, send):
    """
    Ultra-minimal ASGI app optimized for Granian's Rust runtime
    """
    assert scope["type"] == "http"
    
    # Minimal scope extraction
    method = scope["method"]
    path = scope["path"]
    
    # Direct response construction (no framework)
    if path == "/":
        body = b"Hello World"
        content_type = b"text/plain"
    elif path == "/json":
        body = orjson.dumps({"status": "ok", "path": path})
        content_type = b"application/json"
    else:
        body = b"Not Found"
        content_type = b"text/plain"
    
    # Direct ASGI send calls (fastest path)
    await send({
        "type": "http.response.start",
        "status": 200 if path in ("/", "/json") else 404,
        "headers": [
            [b"content-type", content_type],
            [b"content-length", str(len(body)).encode()],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })
```

### Maximum Performance Granian Command
```bash
granian granian_asgi:app \
  --interface asgi \
  --host 0.0.0.0 \
  --port 8000 \
  --workers $(nproc) \
  --runtime-threads 1 \
  --loop uvloop \
  --http 1 \
  --backlog 8192 \
  --no-ws \
  --no-log \
  --no-access-log \
  --http1-keep-alive \
  --http1-pipeline-flush \
  --http1-buffer-size 417792 \
  --process-name granian-worker
```

> **Note:** `--process-name` requires the `granian[pname]` extra: `pip install granian[pname,uvloop]`

**Extreme Optimizations:**
- **`--runtime-threads 1`**: For pure async I/O bound workloads, single Rust I/O thread per worker reduces context switching
- **`--no-log`**: Disables all runtime logging for zero logging overhead
- **`--http 1`**: Locks to HTTP/1 only, avoiding HTTP/2 negotiation overhead
- **`--http1-pipeline-flush`**: Aggregates flushes for pipelined responses
- **`orjson`**: Fastest Python JSON library (use for serialization)
- **Direct ASGI**: Bypass all framework abstractions

---

## Performance Comparison Summary

| Variant | Relative Performance* | Best Use Case |
|---------|----------------------|---------------|
| Gunicorn+Uvicorn · Raw ASGI | **Best for CPU-burst** | **QuantLens default — backtesting, optimization** |
| FastAPI + Gunicorn+Uvicorn | Good | WebSocket required |
| FastAPI + Uvicorn | Baseline | Single-worker development |
| FastAPI + Granian | Mixed | HTTP/2 or Prometheus metrics needed |
| Vanilla ASGI + Granian | Mixed | HTTP/2 or Prometheus metrics needed |

*Absolute RPS depends heavily on hardware, payload, and application logic. See [backend_server.md](backend_server.md) for QuantLens extended benchmark results. Always profile your own workload.

---

## System-Level Optimizations (Apply to All)

```bash
# /etc/sysctl.conf for Linux
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15

# File descriptors
ulimit -n 100000
```

---

## Docker Compose Example

```yaml
version: '3.8'
services:
  # Variant 1: Uvicorn + FastAPI
  fastapi-uvicorn:
    build: .
    command: >
      gunicorn main:app -w 4 -k uvicorn_worker.UvicornWorker
      --bind 0.0.0.0:8000
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 2G
  
  # Variant 2: Granian + FastAPI
  fastapi-granian:
    build: .
    command: >
      granian main:app --interface asgi --workers 4 --runtime-threads 2
      --host 0.0.0.0 --port 8000 --no-ws
    deploy:
      resources:
        limits:
          cpus: '4'
          memory: 1G  # Lower memory usage
  
  # Variant 3 & 4: Vanilla ASGI
  vanilla-granian:
    build: .
    command: >
      granian asgi_app:app --interface asgi --workers 4
      --host 0.0.0.0 --port 8000 --no-log
```

**Recommendation**: Start with **Gunicorn+Uvicorn Raw ASGI** for QuantLens — it delivers the best CPU-burst performance for portfolio optimization workloads. Add **FastAPI on Gunicorn+Uvicorn** only when WebSocket support is explicitly required. See [backend_server.md](backend_server.md) for extended benchmark results.