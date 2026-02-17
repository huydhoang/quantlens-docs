"""Minimal FastAPI application for server benchmarking."""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/json")
async def json_endpoint():
    return JSONResponse(
        {"message": "Hello World", "data": list(range(100))}
    )
