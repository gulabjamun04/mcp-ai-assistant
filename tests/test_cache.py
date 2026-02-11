"""Unit tests for agent.cache — Redis caching layer."""

from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.cache import CACHE_PREFIX, DEFAULT_TTL, RedisCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _expected_key(tool_name: str, arguments: dict) -> str:
    """Reproduce the cache key algorithm."""
    payload = json.dumps(
        {"tool": tool_name, "args": sorted(arguments.items())},
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
    return f"{CACHE_PREFIX}{tool_name}:{digest}"


def _make_cache(ttl: int = DEFAULT_TTL) -> RedisCache:
    """Create a RedisCache with a mocked Redis client."""
    cache = RedisCache("redis://localhost:6379", default_ttl=ttl)
    cache._client = AsyncMock()
    return cache


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self):
        """Successful Redis connection sets client."""
        cache = RedisCache("redis://localhost:6379")
        mock_client = AsyncMock()
        mock_client.ping = AsyncMock(return_value=True)

        with patch("agent.cache.aioredis.from_url", return_value=mock_client):
            await cache.connect()

        assert cache.available is True
        mock_client.ping.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connect_failure_sets_none(self):
        """Failed Redis connection sets client to None (graceful degradation)."""
        cache = RedisCache("redis://localhost:6379")

        with patch(
            "agent.cache.aioredis.from_url",
            side_effect=ConnectionError("refused"),
        ):
            await cache.connect()

        assert cache.available is False

    @pytest.mark.asyncio
    async def test_close(self):
        """Close disconnects the client."""
        cache = _make_cache()
        await cache.close()
        assert cache._client is None


# ---------------------------------------------------------------------------
# Cache get / set
# ---------------------------------------------------------------------------


class TestGetSet:
    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """Returns None on cache miss and increments miss counter."""
        cache = _make_cache()
        cache._client.get = AsyncMock(return_value=None)

        result = await cache.get("web_search__search", {"query": "hello"})

        assert result is None
        assert cache._misses == 1
        assert cache._hits == 0

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Returns cached value on hit and increments hit counter."""
        cache = _make_cache()
        cache._client.get = AsyncMock(return_value='{"results": []}')

        result = await cache.get("web_search__search", {"query": "hello"})

        assert result == '{"results": []}'
        assert cache._hits == 1
        assert cache._misses == 0

    @pytest.mark.asyncio
    async def test_set_stores_with_ttl(self):
        """set() stores the value in Redis with the configured TTL."""
        cache = _make_cache(ttl=300)
        cache._client.setex = AsyncMock()
        tool = "note_manager__save_note"
        args = {"title": "Test"}

        await cache.set(tool, args, '{"id": "abc"}')

        expected_key = _expected_key(tool, args)
        cache._client.setex.assert_awaited_once_with(expected_key, 300, '{"id": "abc"}')

    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_client(self):
        """get() returns None when Redis is not connected."""
        cache = RedisCache("redis://localhost:6379")
        assert cache._client is None

        result = await cache.get("web_search__search", {"query": "test"})
        assert result is None
        # No miss counter increment when Redis is unavailable
        assert cache._misses == 0

    @pytest.mark.asyncio
    async def test_set_noop_when_no_client(self):
        """set() does nothing when Redis is not connected."""
        cache = RedisCache("redis://localhost:6379")
        # Should not raise
        await cache.set("tool", {}, "result")

    @pytest.mark.asyncio
    async def test_get_handles_redis_error(self):
        """get() returns None on Redis errors (graceful degradation)."""
        cache = _make_cache()
        cache._client.get = AsyncMock(side_effect=ConnectionError("lost"))

        result = await cache.get("web_search__search", {"query": "test"})
        assert result is None

    @pytest.mark.asyncio
    async def test_set_handles_redis_error(self):
        """set() silently ignores Redis errors."""
        cache = _make_cache()
        cache._client.setex = AsyncMock(side_effect=ConnectionError("lost"))

        # Should not raise
        await cache.set("tool", {"a": 1}, "result")


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------


class TestKeyGeneration:
    def test_deterministic_key(self):
        """Same inputs produce the same cache key."""
        key1 = RedisCache._make_key("tool_a", {"x": 1, "y": 2})
        key2 = RedisCache._make_key("tool_a", {"y": 2, "x": 1})
        assert key1 == key2

    def test_different_args_different_key(self):
        """Different arguments produce different cache keys."""
        key1 = RedisCache._make_key("tool_a", {"x": 1})
        key2 = RedisCache._make_key("tool_a", {"x": 2})
        assert key1 != key2

    def test_different_tools_different_key(self):
        """Different tool names produce different cache keys."""
        key1 = RedisCache._make_key("tool_a", {"x": 1})
        key2 = RedisCache._make_key("tool_b", {"x": 1})
        assert key1 != key2

    def test_key_has_prefix(self):
        """Cache keys start with the namespace prefix."""
        key = RedisCache._make_key("tool_a", {})
        assert key.startswith(CACHE_PREFIX)


# ---------------------------------------------------------------------------
# Health-check exclusion
# ---------------------------------------------------------------------------


class TestShouldCache:
    def test_normal_tool_cached(self):
        assert RedisCache._should_cache("web_search__search") is True

    def test_health_check_not_cached(self):
        assert RedisCache._should_cache("note_manager__health_check") is False

    def test_bare_health_check_not_cached(self):
        assert RedisCache._should_cache("health_check") is False

    def test_tool_with_health_in_name_cached(self):
        """Tools whose name contains 'health' but isn't exactly health_check."""
        assert RedisCache._should_cache("web_search__health_report") is True


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    @pytest.mark.asyncio
    async def test_stats_empty(self):
        """Stats with no activity."""
        cache = _make_cache()
        cache._client.scan = AsyncMock(return_value=(0, []))

        stats = await cache.stats()
        assert stats == {
            "hits": 0,
            "misses": 0,
            "hit_rate": 0.0,
            "total_keys": 0,
        }

    @pytest.mark.asyncio
    async def test_stats_with_activity(self):
        """Stats reflect hits and misses."""
        cache = _make_cache()
        cache._hits = 3
        cache._misses = 7
        cache._client.scan = AsyncMock(return_value=(0, ["k1", "k2", "k3"]))

        stats = await cache.stats()
        assert stats["hits"] == 3
        assert stats["misses"] == 7
        assert stats["hit_rate"] == 0.3
        assert stats["total_keys"] == 3

    @pytest.mark.asyncio
    async def test_stats_no_client(self):
        """Stats still work when Redis is unavailable."""
        cache = RedisCache("redis://localhost:6379")
        cache._hits = 0
        cache._misses = 5

        stats = await cache.stats()
        assert stats["total_keys"] == 0
        assert stats["misses"] == 5


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------


class TestClear:
    @pytest.mark.asyncio
    async def test_clear_deletes_keys(self):
        """clear() removes all cached keys and resets counters."""
        cache = _make_cache()
        cache._hits = 5
        cache._misses = 10
        cache._client.scan = AsyncMock(return_value=(0, ["mcp_cache:a", "mcp_cache:b"]))
        cache._client.delete = AsyncMock(return_value=2)

        result = await cache.clear()

        assert result == {"cleared": 2}
        assert cache._hits == 0
        assert cache._misses == 0
        cache._client.delete.assert_awaited_once_with("mcp_cache:a", "mcp_cache:b")

    @pytest.mark.asyncio
    async def test_clear_no_keys(self):
        """clear() returns 0 when there are no cached keys."""
        cache = _make_cache()
        cache._client.scan = AsyncMock(return_value=(0, []))

        result = await cache.clear()
        assert result == {"cleared": 0}

    @pytest.mark.asyncio
    async def test_clear_no_client(self):
        """clear() returns 0 when Redis is unavailable."""
        cache = RedisCache("redis://localhost:6379")
        result = await cache.clear()
        assert result == {"cleared": 0}
