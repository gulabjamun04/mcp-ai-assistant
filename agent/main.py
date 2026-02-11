"""FastAPI application for the MCP AI Agent.

Endpoints:
  POST   /chat              — Send a message and get a response
  GET    /tools             — List all available MCP tools
  GET    /health            — Agent and MCP server health status
  POST   /tools/refresh     — Manually trigger tool re-discovery
  GET    /cache/stats       — Cache hit/miss statistics
  DELETE /cache/clear       — Flush all cached tool results
  GET    /analytics/tools   — Tool usage statistics
  GET    /analytics/sessions — Session statistics
  GET    /analytics/recent  — Last 20 tool invocations
  GET    /metrics           — Prometheus metrics
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from agent.agent import AIAgent
from agent.cache import RedisCache
from agent.config import settings
from agent.database import Database
from agent.mcp_client import MCPToolRegistry
from agent.metrics import (
    AVAILABLE_SERVERS,
    AVAILABLE_TOOLS,
    HTTP_DURATION,
    HTTP_REQUESTS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# --- Global instances ---
registry = MCPToolRegistry(settings)
cache = RedisCache(settings.redis_url)
db = Database(settings.database_url)
agent = AIAgent(settings, registry)
_refresh_task: asyncio.Task | None = None

TOOL_REFRESH_INTERVAL = 30  # seconds

# Endpoints excluded from HTTP metrics to avoid cardinality explosion
_METRICS_EXCLUDE = {"/metrics", "/openapi.json", "/docs", "/redoc"}


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record HTTP request count and duration for Prometheus."""

    async def dispatch(self, request: Request, call_next):
        """Wrap each request with timing and counting."""
        path = request.url.path
        if path in _METRICS_EXCLUDE:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start

        HTTP_REQUESTS.labels(
            method=request.method,
            endpoint=path,
            status_code=response.status_code,
        ).inc()
        HTTP_DURATION.labels(endpoint=path).observe(elapsed)
        return response


async def _background_refresh() -> None:
    """Periodically re-discover MCP tools."""
    while True:
        await asyncio.sleep(TOOL_REFRESH_INTERVAL)
        try:
            changes = await registry.refresh_tools()
            if changes["added"] or changes["removed"]:
                logger.info(
                    "Tool refresh: added=%s removed=%s",
                    changes["added"],
                    changes["removed"],
                )
        except Exception as e:
            logger.warning("Background tool refresh failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: connect cache + db, discover tools, start background refresh."""
    global _refresh_task
    logger.info("Connecting to Redis cache...")
    await cache.connect()
    registry.cache = cache
    logger.info("Connecting to PostgreSQL...")
    await db.init()
    registry.db = db
    agent.db = db
    logger.info("Starting agent — discovering MCP tools...")
    await registry.discover_tools()
    AVAILABLE_TOOLS.set(len(registry.tools))
    healthy = 0
    for s in settings.mcp_servers:
        status = await registry.check_server_health(s)
        if status["status"] == "healthy":
            healthy += 1
    AVAILABLE_SERVERS.set(healthy)
    logger.info("Tool discovery complete. Starting background refresh task.")
    _refresh_task = asyncio.create_task(_background_refresh())
    yield
    if _refresh_task:
        _refresh_task.cancel()
        try:
            await _refresh_task
        except asyncio.CancelledError:
            pass
    await cache.close()
    await db.close()
    logger.info("Agent shut down.")


app = FastAPI(title="MCP AI Agent", version="1.0.0", lifespan=lifespan)

app.add_middleware(MetricsMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request / Response models ---


class ChatRequest(BaseModel):
    """Chat endpoint request body."""

    message: str
    session_id: str


class ChatResponse(BaseModel):
    """Chat endpoint response body."""

    response: str
    tools_used: list[str]
    latency_ms: float


class ToolInfoResponse(BaseModel):
    """Single tool info in /tools response."""

    name: str
    description: str
    server: str


# --- Endpoints ---


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Send a message to the AI agent and get a response."""
    result = await asyncio.wait_for(
        agent.chat(request.message, request.session_id),
        timeout=180,
    )
    return ChatResponse(
        response=result.response,
        tools_used=result.tools_used,
        latency_ms=result.latency_ms,
    )


@app.get("/tools", response_model=list[ToolInfoResponse])
async def list_tools() -> list[ToolInfoResponse]:
    """List all available MCP tools."""
    return [
        ToolInfoResponse(name=t.name, description=t.description, server=t.server_name)
        for t in registry.tools.values()
    ]


@app.get("/health")
async def health() -> dict[str, Any]:
    """Check health of the agent and all MCP servers."""
    server_statuses = []
    for server in settings.mcp_servers:
        status = await registry.check_server_health(server)
        server_statuses.append(status)

    healthy_count = sum(1 for s in server_statuses if s["status"] == "healthy")
    total = len(server_statuses)

    return {
        "agent": "healthy",
        "model": settings.ollama_model,
        "tools_count": len(registry.tools),
        "servers": server_statuses,
        "overall": "healthy" if healthy_count == total else "degraded",
    }


@app.post("/tools/refresh")
async def refresh_tools() -> dict[str, Any]:
    """Manually trigger tool re-discovery across all MCP servers."""
    changes = await registry.refresh_tools()
    AVAILABLE_TOOLS.set(len(registry.tools))
    return {"status": "ok", "changes": changes}


@app.get("/cache/stats")
async def cache_stats() -> dict[str, Any]:
    """Return cache hit/miss statistics."""
    return await cache.stats()


@app.delete("/cache/clear")
async def cache_clear() -> dict[str, int]:
    """Flush all cached tool results."""
    return await cache.clear()


# --- Analytics endpoints ---


@app.get("/analytics/tools")
async def analytics_tools() -> list[dict[str, Any]]:
    """Aggregated tool usage statistics."""
    return await db.get_tool_analytics()


@app.get("/analytics/sessions")
async def analytics_sessions() -> dict[str, Any]:
    """Session statistics: total, avg messages, active in last hour."""
    return await db.get_session_analytics()


@app.get("/analytics/recent")
async def analytics_recent() -> list[dict[str, Any]]:
    """Last 20 tool invocations with details."""
    return await db.get_recent_invocations()


@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
