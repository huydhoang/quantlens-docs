"""Minimal FastAPI application for server benchmarking.

Three-tier endpoints for fair comparison:
  /json      — minimal JSON, pure server overhead
  /compute   — synchronous CPU work (sorting random data)
  /async-io  — simulated async I/O (asyncio.sleep)
  /health    — health check
"""

import asyncio
import json
import random

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Pre-encode the static JSON body so both apps return byte-identical responses
_JSON_BODY = json.dumps({"message": "Hello, World!"}).encode()


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/json")
async def json_endpoint():
    return Response(content=_JSON_BODY, media_type="application/json")


@app.get("/compute")
async def compute():
    """Synchronous CPU-bound work: sort 5 000 random floats."""
    rng = random.Random(42)
    data = [rng.random() for _ in range(5_000)]
    sorted(data)
    return {"sorted_count": len(data)}


@app.get("/async-io")
async def async_io():
    """Simulated async I/O — 10 ms sleep."""
    await asyncio.sleep(0.01)
    return {"slept_ms": 10}
