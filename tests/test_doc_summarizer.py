"""Tests for the Document Summarizer MCP server.

Unit tests for helpers and tools (with mocked Ollama), plus integration tests
that start the MCP server as a subprocess and exercise every tool via the MCP
client SDK.
"""

import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.client.sse import sse_client

from mcp_servers.doc_summarizer.server import (
    MAX_TEXT_LENGTH,
    _parse_key_points,
    _strip_thinking_tags,
    _validate_text,
    extract_key_points,
    health_check,
    summarize_text,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_MODULE = "mcp_servers.doc_summarizer.server"
SERVER_URL = "http://localhost:8003/sse"

SAMPLE_TEXT = (
    "Artificial intelligence (AI) is the simulation of human intelligence "
    "processes by computer systems. These processes include learning, "
    "reasoning, and self-correction. AI is used in a wide range of "
    "applications including healthcare, finance, and transportation. "
    "Machine learning is a subset of AI that focuses on building systems "
    "that learn from data. Deep learning is a further subset that uses "
    "neural networks with many layers."
)


# ===================================================================
# UNIT TESTS — helpers
# ===================================================================


class TestStripThinkingTags:
    def test_removes_thinking_block(self) -> None:
        text = "<think>internal reasoning</think>The actual answer."
        assert _strip_thinking_tags(text) == "The actual answer."

    def test_no_tags_unchanged(self) -> None:
        text = "Just a normal response."
        assert _strip_thinking_tags(text) == "Just a normal response."

    def test_multiple_blocks(self) -> None:
        text = "<think>first</think>Hello <think>second</think>world."
        assert _strip_thinking_tags(text) == "Hello world."


class TestValidateText:
    def test_valid_text(self) -> None:
        assert _validate_text("Some valid text.") is None

    def test_empty_string(self) -> None:
        err = _validate_text("")
        assert err is not None
        assert "empty" in err.lower()

    def test_whitespace_only(self) -> None:
        err = _validate_text("   \n\t  ")
        assert err is not None
        assert "empty" in err.lower()

    def test_too_long(self) -> None:
        err = _validate_text("x" * (MAX_TEXT_LENGTH + 1))
        assert err is not None
        assert "too long" in err.lower()


class TestParseKeyPoints:
    def test_numbered_list(self) -> None:
        text = "1. First point\n2. Second point\n3. Third point"
        points = _parse_key_points(text, 5)
        assert points == ["First point", "Second point", "Third point"]

    def test_bulleted_list(self) -> None:
        text = "- Alpha\n- Beta\n- Gamma"
        points = _parse_key_points(text, 5)
        assert points == ["Alpha", "Beta", "Gamma"]

    def test_max_count_respected(self) -> None:
        text = "1. A\n2. B\n3. C\n4. D\n5. E"
        points = _parse_key_points(text, 3)
        assert len(points) == 3

    def test_fallback_to_sentences(self) -> None:
        text = "First sentence. Second sentence. Third sentence."
        points = _parse_key_points(text, 5)
        assert len(points) >= 2
        assert "First sentence." in points[0]


# ===================================================================
# UNIT TESTS — tools (mocked _call_ollama)
# ===================================================================


class TestSummarizeText:
    def test_success(self) -> None:
        """summarize_text returns a summary on success."""
        mock_response = {"response": "AI simulates human intelligence."}

        async def _run() -> dict:
            with patch(
                "mcp_servers.doc_summarizer.server._call_ollama",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                return await summarize_text(SAMPLE_TEXT, max_length=200)

        result = anyio.run(_run)
        assert result["summary"] == "AI simulates human intelligence."
        assert result["summary_length"] > 0
        assert result["text_length"] == len(SAMPLE_TEXT)
        assert "error" not in result

    def test_empty_text_error(self) -> None:
        """Empty text should return an error without calling Ollama."""

        async def _run() -> dict:
            return await summarize_text("", max_length=200)

        result = anyio.run(_run)
        assert "error" in result
        assert "empty" in result["error"].lower()

    def test_too_long_error(self) -> None:
        """Text exceeding MAX_TEXT_LENGTH should return an error."""

        async def _run() -> dict:
            return await summarize_text("x" * (MAX_TEXT_LENGTH + 1))

        result = anyio.run(_run)
        assert "error" in result
        assert "too long" in result["error"].lower()

    def test_ollama_error(self) -> None:
        """Ollama errors should be forwarded in the result."""
        mock_response = {"error": "Cannot connect to Ollama"}

        async def _run() -> dict:
            with patch(
                "mcp_servers.doc_summarizer.server._call_ollama",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                return await summarize_text(SAMPLE_TEXT)

        result = anyio.run(_run)
        assert "error" in result
        assert "ollama" in result["error"].lower()


class TestExtractKeyPoints:
    def test_success(self) -> None:
        """extract_key_points returns a list of points on success."""
        mock_response = {
            "response": "1. AI simulates intelligence\n2. ML learns from data\n3. DL uses neural nets"
        }

        async def _run() -> dict:
            with patch(
                "mcp_servers.doc_summarizer.server._call_ollama",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                return await extract_key_points(SAMPLE_TEXT, num_points=3)

        result = anyio.run(_run)
        assert result["count"] == 3
        assert len(result["key_points"]) == 3
        assert result["text_length"] == len(SAMPLE_TEXT)
        assert "error" not in result

    def test_empty_text_error(self) -> None:
        """Empty text should return an error."""

        async def _run() -> dict:
            return await extract_key_points("")

        result = anyio.run(_run)
        assert "error" in result
        assert result["count"] == 0

    def test_num_points_clamped(self) -> None:
        """num_points outside 1-15 should be clamped."""
        mock_response = {"response": "1. Only one point"}

        async def _run() -> dict:
            with patch(
                "mcp_servers.doc_summarizer.server._call_ollama",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                return await extract_key_points(SAMPLE_TEXT, num_points=100)

        result = anyio.run(_run)
        # Should succeed (clamped to 15), not error
        assert "error" not in result

    def test_ollama_error(self) -> None:
        """Ollama errors should be forwarded."""
        mock_response = {"error": "Ollama request timed out"}

        async def _run() -> dict:
            with patch(
                "mcp_servers.doc_summarizer.server._call_ollama",
                new_callable=AsyncMock,
                return_value=mock_response,
            ):
                return await extract_key_points(SAMPLE_TEXT)

        result = anyio.run(_run)
        assert "error" in result
        assert result["count"] == 0


class TestHealthCheck:
    def test_healthy(self) -> None:
        """health_check returns healthy when Ollama is available."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "qwen3:1.7b"}]}

        async def _run() -> dict:
            with patch(
                "mcp_servers.doc_summarizer.server.httpx.AsyncClient"
            ) as MockClient:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(return_value=mock_resp)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client
                return await health_check()

        result = anyio.run(_run)
        assert result["status"] == "healthy"
        assert result["server"] == "doc-summarizer"
        assert result["ollama_status"] == "available"
        assert result["ollama_model"] == "qwen3:1.7b"
        assert "timestamp" in result

    def test_degraded_on_connect_error(self) -> None:
        """health_check returns degraded when Ollama is unreachable."""

        async def _run() -> dict:
            with patch(
                "mcp_servers.doc_summarizer.server.httpx.AsyncClient"
            ) as MockClient:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=False)
                MockClient.return_value = mock_client
                return await health_check()

        result = anyio.run(_run)
        assert result["status"] == "degraded"
        assert result["ollama_status"] == "unreachable"


# ===================================================================
# INTEGRATION TESTS — MCP client ↔ server (requires Ollama)
# ===================================================================


def _parse_tool_response(result) -> dict:
    """Extract the JSON dict from a CallToolResult."""
    text = result.content[0].text
    return json.loads(text)


def _ollama_is_reachable() -> bool:
    """Check if Ollama is running and has the required model."""
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=5)
        if resp.status_code != 200:
            return False
        models = resp.json().get("models", [])
        return any("qwen3:1.7b" in m.get("name", "") for m in models)
    except Exception:
        return False


@pytest.fixture(scope="module")
def doc_server():
    """Start the Doc Summarizer MCP server, yield, then stop.

    Skips all integration tests if Ollama is not reachable.
    """
    if not _ollama_is_reachable():
        pytest.skip(
            "Ollama with qwen3:1.7b is not reachable — skipping integration tests"
        )

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
    """Integration tests that talk to the live MCP server + Ollama."""

    # ------ summarize_text ------

    def test_summarize_text_success(self, doc_server) -> None:
        """summarize_text should return a non-empty summary."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "summarize_text",
                        {"text": SAMPLE_TEXT, "max_length": 200},
                    )
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["text_length"] == len(SAMPLE_TEXT)
        assert len(data["summary"]) > 0
        assert "error" not in data

    def test_summarize_text_empty_error(self, doc_server) -> None:
        """Empty text should return an error via MCP."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "summarize_text",
                        {"text": "   "},
                    )
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert "error" in data

    # ------ extract_key_points ------

    def test_extract_key_points_success(self, doc_server) -> None:
        """extract_key_points should return a list of points."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "extract_key_points",
                        {"text": SAMPLE_TEXT, "num_points": 3},
                    )
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["count"] > 0
        assert len(data["key_points"]) > 0
        assert "error" not in data

    def test_extract_key_points_empty_error(self, doc_server) -> None:
        """Empty text should return an error via MCP."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "extract_key_points",
                        {"text": ""},
                    )
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert "error" in data

    # ------ health_check ------

    def test_health_check(self, doc_server) -> None:
        """health_check should return healthy status with model info."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool("health_check", {})
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["status"] == "healthy"
        assert data["server"] == "doc-summarizer"
        assert data["ollama_model"] == "qwen3:1.7b"
        assert "timestamp" in data
