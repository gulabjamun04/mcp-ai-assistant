"""Thin HTTP client for the FastAPI agent backend.

All functions return parsed JSON (dicts/lists) or raise on failure.
Uses requests (synchronous) since Streamlit reruns are synchronous.
"""

from __future__ import annotations

import os
from typing import Any

import requests

BASE_URL = os.getenv("AGENT_API_URL", "http://localhost:8000")
_TIMEOUT = 180  # seconds — LLM summarization can be very slow on CPU


def chat(message: str, session_id: str) -> dict[str, Any]:
    """POST /chat — send a message and get the agent's response."""
    resp = requests.post(
        f"{BASE_URL}/chat",
        json={"message": message, "session_id": session_id},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_tools() -> list[dict[str, Any]]:
    """GET /tools — list available MCP tools."""
    resp = requests.get(f"{BASE_URL}/tools", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_health() -> dict[str, Any]:
    """GET /health — agent and server health status."""
    resp = requests.get(f"{BASE_URL}/health", timeout=10)
    resp.raise_for_status()
    return resp.json()


def refresh_tools() -> dict[str, Any]:
    """POST /tools/refresh — re-discover MCP tools."""
    resp = requests.post(f"{BASE_URL}/tools/refresh", timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_cache_stats() -> dict[str, Any]:
    """GET /cache/stats — cache hit/miss statistics."""
    resp = requests.get(f"{BASE_URL}/cache/stats", timeout=10)
    resp.raise_for_status()
    return resp.json()


def clear_cache() -> dict[str, Any]:
    """DELETE /cache/clear — flush cached tool results."""
    resp = requests.delete(f"{BASE_URL}/cache/clear", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_tool_analytics() -> list[dict[str, Any]]:
    """GET /analytics/tools — per-tool usage statistics."""
    resp = requests.get(f"{BASE_URL}/analytics/tools", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_session_analytics() -> dict[str, Any]:
    """GET /analytics/sessions — session statistics."""
    resp = requests.get(f"{BASE_URL}/analytics/sessions", timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_recent_invocations() -> list[dict[str, Any]]:
    """GET /analytics/recent — last 20 tool invocations."""
    resp = requests.get(f"{BASE_URL}/analytics/recent", timeout=10)
    resp.raise_for_status()
    return resp.json()
