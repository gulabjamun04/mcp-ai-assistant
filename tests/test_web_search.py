"""Tests for the Web Search MCP server.

Unit tests for helpers and tools, plus integration tests that start the
MCP server as a subprocess and exercise every tool via the MCP client SDK.
"""

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.sse import sse_client

from mcp_servers.web_search.server import (
    _check_rate_limit,
    _fetch_page_text,
    _search_timestamps,
    _validate_url,
    fetch_url,
    health_check,
    web_search,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_MODULE = "mcp_servers.web_search.server"
SERVER_URL = "http://localhost:8002/sse"


# ===================================================================
# UNIT TESTS — helpers
# ===================================================================


class TestValidateUrl:
    def test_valid_https(self) -> None:
        assert _validate_url("https://example.com") is None

    def test_valid_http(self) -> None:
        assert _validate_url("http://example.com") is None

    def test_invalid_scheme(self) -> None:
        err = _validate_url("ftp://example.com")
        assert err is not None
        assert "scheme" in err.lower()

    def test_missing_scheme(self) -> None:
        err = _validate_url("example.com")
        assert err is not None

    def test_empty_string(self) -> None:
        err = _validate_url("")
        assert err is not None


class TestFetchPageText:
    def test_returns_text(self) -> None:
        text = _fetch_page_text("https://example.com")
        assert len(text) > 0

    def test_truncation(self) -> None:
        text = _fetch_page_text("https://example.com", max_chars=50)
        assert len(text) <= 50

    def test_strips_scripts(self) -> None:
        text = _fetch_page_text("https://example.com")
        assert "<script" not in text
        assert "<style" not in text


class TestRateLimit:
    def setup_method(self) -> None:
        """Clear rate-limit state before each test."""
        _search_timestamps.clear()

    def test_allows_under_limit(self) -> None:
        for _ in range(10):
            assert _check_rate_limit() is True

    def test_blocks_over_limit(self) -> None:
        for _ in range(10):
            _check_rate_limit()
        assert _check_rate_limit() is False

    def teardown_method(self) -> None:
        _search_timestamps.clear()


# ===================================================================
# UNIT TESTS — tools (direct calls)
# ===================================================================


@pytest.mark.integration
class TestWebSearch:
    def setup_method(self) -> None:
        _search_timestamps.clear()

    def test_returns_results(self) -> None:
        result = web_search("Python programming language", num_results=3)
        assert result["query"] == "Python programming language"
        assert result["count"] > 0
        assert len(result["results"]) <= 3
        first = result["results"][0]
        assert "title" in first
        assert "url" in first
        assert "snippet" in first

    def test_num_results_clamped(self) -> None:
        result = web_search("test", num_results=50)
        assert result["count"] <= 10

    def test_empty_query(self) -> None:
        result = web_search("")
        assert result["count"] == 0
        assert "error" in result

    def teardown_method(self) -> None:
        _search_timestamps.clear()


@pytest.mark.integration
class TestFetchUrl:
    def test_fetch_valid_url(self) -> None:
        result = fetch_url("https://example.com")
        assert result["url"] == "https://example.com"
        assert len(result["content"]) > 0
        assert result["length"] > 0
        assert "error" not in result

    def test_fetch_content_within_limit(self) -> None:
        result = fetch_url("https://example.com")
        assert result["length"] <= 5000

    def test_fetch_invalid_url(self) -> None:
        result = fetch_url("https://this-domain-does-not-exist-xyz123.com")
        assert "error" in result

    def test_fetch_bad_scheme(self) -> None:
        result = fetch_url("ftp://example.com")
        assert "error" in result
        assert "scheme" in result["error"].lower()

    def test_fetch_no_scheme(self) -> None:
        result = fetch_url("not-a-url")
        assert "error" in result


class TestHealthCheck:
    def test_structure(self) -> None:
        result = health_check()
        assert result["status"] == "healthy"
        assert result["server"] == "web-search"
        assert "timestamp" in result


# ===================================================================
# INTEGRATION TESTS — MCP client ↔ server
# ===================================================================


def _parse_tool_response(result) -> dict:
    """Extract the JSON dict from a CallToolResult."""
    text = result.content[0].text
    return json.loads(text)


@pytest.fixture(scope="module")
def web_server():
    """Start the Web Search MCP server, yield, then stop."""
    proc = subprocess.Popen(
        [sys.executable, "-m", SERVER_MODULE],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for uvicorn to be ready
    time.sleep(3)
    assert proc.poll() is None, f"Server failed to start: {proc.stderr.read().decode()}"

    yield proc

    # Teardown
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.integration
class TestMCPIntegration:
    """Integration tests that talk to the live MCP server."""

    # ------ web_search ------

    def test_web_search_returns_results(self, web_server) -> None:
        """web_search should return structured results."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "web_search",
                        {"query": "Python programming", "num_results": 3},
                    )
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["query"] == "Python programming"
        assert data["count"] > 0
        assert len(data["results"]) <= 3

    def test_web_search_empty_query(self, web_server) -> None:
        """Empty query should return an error, not crash."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool("web_search", {"query": ""})
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["count"] == 0
        assert "error" in data

    # ------ fetch_url ------

    def test_fetch_url_valid(self, web_server) -> None:
        """fetch_url should return page text for a valid URL."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "fetch_url", {"url": "https://example.com"}
                    )
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["url"] == "https://example.com"
        assert data["length"] > 0
        assert "error" not in data

    def test_fetch_url_invalid(self, web_server) -> None:
        """fetch_url with a bad domain should return an error."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "fetch_url",
                        {"url": "https://this-domain-does-not-exist-xyz123.com"},
                    )
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert "error" in data

    def test_fetch_url_bad_scheme(self, web_server) -> None:
        """fetch_url with ftp:// should return a validation error."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "fetch_url", {"url": "ftp://example.com"}
                    )
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert "error" in data
        assert "scheme" in data["error"].lower()

    # ------ health_check ------

    def test_health_check(self, web_server) -> None:
        """health_check should return healthy status."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool("health_check", {})
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["status"] == "healthy"
        assert data["server"] == "web-search"
        assert "timestamp" in data
