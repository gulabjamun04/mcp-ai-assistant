"""Prometheus metrics for the MCP AI Agent.

All metric objects are defined here so they can be imported from any module.
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Tool invocation metrics
# ---------------------------------------------------------------------------

TOOL_INVOCATIONS = Counter(
    "mcp_tool_invocations_total",
    "Total number of MCP tool invocations",
    ["tool_name", "server_name", "status", "cache_hit"],
)

TOOL_DURATION = Histogram(
    "mcp_tool_duration_seconds",
    "Duration of MCP tool calls in seconds",
    ["tool_name", "server_name"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

# ---------------------------------------------------------------------------
# Cache metrics
# ---------------------------------------------------------------------------

CACHE_OPERATIONS = Counter(
    "mcp_cache_operations_total",
    "Total cache operations",
    ["operation"],  # hit, miss, clear
)

# ---------------------------------------------------------------------------
# Session metrics
# ---------------------------------------------------------------------------

ACTIVE_SESSIONS = Gauge(
    "mcp_active_sessions",
    "Number of active chat sessions",
)

# ---------------------------------------------------------------------------
# HTTP request metrics
# ---------------------------------------------------------------------------

HTTP_REQUESTS = Counter(
    "mcp_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

HTTP_DURATION = Histogram(
    "mcp_http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["endpoint"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 5.0, 30.0, 60.0, 120.0),
)

# ---------------------------------------------------------------------------
# Discovery metrics
# ---------------------------------------------------------------------------

AVAILABLE_TOOLS = Gauge(
    "mcp_available_tools",
    "Number of currently available MCP tools",
)

AVAILABLE_SERVERS = Gauge(
    "mcp_available_servers",
    "Number of healthy MCP servers",
)
