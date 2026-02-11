"""MCP Tool Discovery and Execution Client.

Connects to MCP servers via SSE, discovers available tools,
converts them to LangChain-compatible tools, and executes tool calls.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.tools import StructuredTool
from mcp import ClientSession
from mcp.client.sse import sse_client
from pydantic import BaseModel, Field, create_model

from agent.cache import RedisCache
from agent.config import MCPServerConfig, Settings
from agent.metrics import TOOL_DURATION, TOOL_INVOCATIONS

logger = logging.getLogger(__name__)

# Context variable for tracking the current session across async tool calls.
# Set by AIAgent.chat() before invoking the LangGraph agent so that tool
# invocation logging can attribute calls to the correct session.
_current_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_session_id", default=None
)

# Timeout for SSE connections (seconds)
SSE_CONNECT_TIMEOUT = 5
SSE_READ_TIMEOUT = 120


@dataclass
class ToolInfo:
    """Metadata about a discovered MCP tool."""

    name: str  # Namespaced: server_name__tool_name
    mcp_name: str  # Original name on the MCP server
    description: str
    server_name: str
    server_url: str
    input_schema: dict[str, Any]


class MCPToolRegistry:
    """Registry that discovers and manages MCP tools across all servers."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.cache: Optional[RedisCache] = None
        self.db: Any = None  # Database instance, set during startup
        self._tools: dict[str, ToolInfo] = {}
        self._langchain_tools: list[StructuredTool] = []

    @property
    def tools(self) -> dict[str, ToolInfo]:
        """All discovered tools keyed by name."""
        return dict(self._tools)

    @property
    def langchain_tools(self) -> list[StructuredTool]:
        """All tools as LangChain StructuredTool instances."""
        return list(self._langchain_tools)

    async def discover_tools(self) -> None:
        """Connect to all configured MCP servers and discover their tools."""
        new_tools: dict[str, ToolInfo] = {}

        for server in self.settings.mcp_servers:
            try:
                server_tools = await self._discover_server_tools(server)
                for tool in server_tools:
                    new_tools[tool.name] = tool
                logger.info(
                    "Discovered %d tools from %s", len(server_tools), server.name
                )
            except Exception as e:
                logger.warning(
                    "Failed to connect to %s (%s): %s", server.name, server.url, e
                )

        self._tools = new_tools
        self._langchain_tools = [
            self._to_langchain_tool(t) for t in self._tools.values()
        ]
        logger.info("Total tools available: %d", len(self._tools))

    async def refresh_tools(self) -> dict[str, Any]:
        """Re-discover all tools. Returns summary of changes."""
        old_names = set(self._tools.keys())
        await self.discover_tools()
        new_names = set(self._tools.keys())

        return {
            "added": sorted(new_names - old_names),
            "removed": sorted(old_names - new_names),
            "total": sorted(new_names),
        }

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool on its MCP server and return the text result.

        If a Redis cache is attached, checks it before calling the MCP
        server and stores successful results afterwards.  Tool invocations
        are logged to PostgreSQL when a Database is attached.
        """
        if tool_name not in self._tools:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})

        tool_info = self._tools[tool_name]
        start = time.perf_counter()

        # --- Cache check ---
        if self.cache:
            cached = await self.cache.get(tool_name, arguments)
            if cached is not None:
                self._log_invocation(
                    tool_info,
                    arguments,
                    cached,
                    start,
                    cache_hit=True,
                    status="success",
                )
                return cached

        sse_url = f"{tool_info.server_url}/sse"

        try:
            async with sse_client(
                sse_url,
                timeout=SSE_CONNECT_TIMEOUT,
                sse_read_timeout=SSE_READ_TIMEOUT,
            ) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool_info.mcp_name, arguments)

                    texts = []
                    for block in result.content:
                        if hasattr(block, "text"):
                            texts.append(block.text)

                    response = "\n".join(texts) if texts else "{}"

                    if result.isError:
                        self._log_invocation(
                            tool_info,
                            arguments,
                            response,
                            start,
                            cache_hit=False,
                            status="error",
                        )
                        return json.dumps({"error": response})

                    # --- Cache store ---
                    if self.cache:
                        await self.cache.set(tool_name, arguments, response)

                    self._log_invocation(
                        tool_info,
                        arguments,
                        response,
                        start,
                        cache_hit=False,
                        status="success",
                    )
                    return response

        except Exception as e:
            logger.error("Error calling tool %s: %s", tool_name, e)
            error_response = json.dumps({"error": f"Tool execution failed: {e}"})
            self._log_invocation(
                tool_info,
                arguments,
                error_response,
                start,
                cache_hit=False,
                status="error",
            )
            return error_response

    def _log_invocation(
        self,
        tool_info: ToolInfo,
        arguments: dict[str, Any],
        response: str,
        start: float,
        *,
        cache_hit: bool,
        status: str,
    ) -> None:
        """Fire-and-forget tool invocation logging to PostgreSQL + Prometheus."""
        latency_ms = (time.perf_counter() - start) * 1000
        latency_s = latency_ms / 1000

        # Prometheus metrics (always recorded)
        TOOL_INVOCATIONS.labels(
            tool_name=tool_info.name,
            server_name=tool_info.server_name,
            status=status,
            cache_hit=str(cache_hit),
        ).inc()
        TOOL_DURATION.labels(
            tool_name=tool_info.name,
            server_name=tool_info.server_name,
        ).observe(latency_s)

        # PostgreSQL logging (fire-and-forget)
        if not self.db:
            return
        session_id = _current_session_id.get()
        if not session_id:
            return
        asyncio.create_task(
            self.db.log_tool_invocation(
                session_id=session_id,
                tool_name=tool_info.name,
                server_name=tool_info.server_name,
                input_data=arguments,
                output_data=response,
                latency_ms=latency_ms,
                cache_hit=cache_hit,
                status=status,
            )
        )

    async def check_server_health(self, server: MCPServerConfig) -> dict[str, Any]:
        """Check if an MCP server is reachable."""
        try:
            sse_url = f"{server.url}/sse"
            async with sse_client(sse_url, timeout=3) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return {
                        "name": server.name,
                        "status": "healthy",
                        "url": server.url,
                    }
        except Exception as e:
            return {
                "name": server.name,
                "status": "unhealthy",
                "url": server.url,
                "error": str(e),
            }

    async def _discover_server_tools(self, server: MCPServerConfig) -> list[ToolInfo]:
        """Discover tools from a single MCP server via SSE."""
        sse_url = f"{server.url}/sse"

        async with sse_client(sse_url, timeout=SSE_CONNECT_TIMEOUT) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()

                return [
                    ToolInfo(
                        name=f"{server.name}__{tool.name}",
                        mcp_name=tool.name,
                        description=tool.description or "",
                        server_name=server.name,
                        server_url=server.url,
                        input_schema=tool.inputSchema,
                    )
                    for tool in result.tools
                ]

    def _to_langchain_tool(self, tool_info: ToolInfo) -> StructuredTool:
        """Convert an MCP ToolInfo to a LangChain StructuredTool."""
        args_model = _json_schema_to_pydantic(tool_info.input_schema, tool_info.name)

        # Capture variables for the closure
        registry = self
        name = tool_info.name

        async def _invoke(**kwargs: Any) -> str:
            return await registry.call_tool(name, kwargs)

        return StructuredTool.from_function(
            func=None,
            coroutine=_invoke,
            name=tool_info.name,
            description=tool_info.description or "No description",
            args_schema=args_model,
        )


# ---------------------------------------------------------------------------
# JSON Schema -> Pydantic model conversion
# ---------------------------------------------------------------------------


def _json_schema_to_pydantic(schema: dict[str, Any], tool_name: str) -> type[BaseModel]:
    """Convert a JSON Schema (from MCP tool) to a Pydantic model."""
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    if not properties:
        return create_model(f"{tool_name}_Args")

    fields: dict[str, Any] = {}
    for prop_name, prop_def in properties.items():
        python_type = _resolve_type(prop_def)
        desc = prop_def.get("description", "")

        if prop_name in required:
            fields[prop_name] = (
                python_type,
                Field(description=desc),
            )
        else:
            default = prop_def.get("default")
            fields[prop_name] = (
                Optional[python_type],
                Field(default=default, description=desc),
            )

    return create_model(f"{tool_name}_Args", **fields)


def _resolve_type(prop_def: dict[str, Any]) -> type:
    """Resolve the Python type from a JSON Schema property definition."""
    # Handle anyOf (Pydantic's encoding for X | None)
    if "anyOf" in prop_def:
        non_null = [p for p in prop_def["anyOf"] if p.get("type") != "null"]
        if non_null:
            return _map_json_type(non_null[0])
        return str

    return _map_json_type(prop_def)


def _map_json_type(prop_def: dict[str, Any]) -> type:
    """Map a JSON Schema type to a Python type."""
    json_type = prop_def.get("type", "string")

    type_map: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    return type_map.get(json_type, str)
