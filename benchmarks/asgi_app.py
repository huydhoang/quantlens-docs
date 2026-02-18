"""Minimal vanilla ASGI application for server benchmarking.

Three-tier endpoints for fair comparison:
  /json      — minimal JSON, pure server overhead
  /compute   — synchronous CPU work (sorting random data)
  /async-io  — simulated async I/O (asyncio.sleep)
  /health    — health check
"""

import asyncio
import json
import random

# Pre-allocate constant responses
_HEALTH_BODY = b'{"status":"ok"}'
_JSON_BODY = json.dumps({"message": "Hello, World!"}).encode()
_NOT_FOUND_BODY = b"Not Found"

_HEALTH_START = {
    "type": "http.response.start",
    "status": 200,
    "headers": [
        [b"content-type", b"application/json"],
        [b"content-length", str(len(_HEALTH_BODY)).encode()],
    ],
}
_JSON_START = {
    "type": "http.response.start",
    "status": 200,
    "headers": [
        [b"content-type", b"application/json"],
        [b"content-length", str(len(_JSON_BODY)).encode()],
    ],
}
_NOT_FOUND_START = {
    "type": "http.response.start",
    "status": 404,
    "headers": [
        [b"content-type", b"text/plain"],
        [b"content-length", str(len(_NOT_FOUND_BODY)).encode()],
    ],
}


def _cpu_work():
    """Deterministic CPU-bound work: sort 5 000 random floats."""
    rng = random.Random(42)
    data = [rng.random() for _ in range(5_000)]
    sorted(data)
    return json.dumps({"sorted_count": len(data)}).encode()


async def app(scope, receive, send):
    if scope["type"] != "http":
        return

    path = scope["path"]

    if path == "/health":
        await send(_HEALTH_START)
        await send({"type": "http.response.body", "body": _HEALTH_BODY})
    elif path == "/json":
        await send(_JSON_START)
        await send({"type": "http.response.body", "body": _JSON_BODY})
    elif path == "/compute":
        body = _cpu_work()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
    elif path == "/async-io":
        await asyncio.sleep(0.01)
        body = json.dumps({"slept_ms": 10}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    [b"content-type", b"application/json"],
                    [b"content-length", str(len(body)).encode()],
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
    else:
        await send(_NOT_FOUND_START)
        await send({"type": "http.response.body", "body": _NOT_FOUND_BODY})
