"""Minimal vanilla ASGI application for server benchmarking."""

import json


async def app(scope, receive, send):
    if scope["type"] != "http":
        return

    path = scope["path"]

    if path == "/health":
        body = b'{"status":"ok"}'
        status = 200
        content_type = b"application/json"
    elif path == "/json":
        body = json.dumps(
            {"message": "Hello World", "data": list(range(100))}
        ).encode()
        status = 200
        content_type = b"application/json"
    else:
        body = b"Not Found"
        status = 404
        content_type = b"text/plain"

    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                [b"content-type", content_type],
                [b"content-length", str(len(body)).encode()],
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
