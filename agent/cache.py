"""Redis caching layer for MCP tool results.

Caches tool call results with configurable TTL. Handles Redis
being unavailable gracefully — the agent continues to work without
caching. Health-check tools are never cached.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

from agent.metrics import CACHE_OPERATIONS

logger = logging.getLogger(__name__)

CACHE_PREFIX = "mcp_cache:"
DEFAULT_TTL = 600  # 10 minutes


class RedisCache:
    """Async Redis cache for MCP tool results."""

    def __init__(self, redis_url: str, default_ttl: int = DEFAULT_TTL) -> None:
        self._redis_url = redis_url
        self._default_ttl = default_ttl
        self._client: Optional[aioredis.Redis] = None
        self._hits = 0
        self._misses = 0

    @property
    def available(self) -> bool:
        """Whether the Redis connection is active."""
        return self._client is not None

    async def connect(self) -> None:
        """Connect to Redis. Non-fatal if Redis is unavailable."""
        try:
            self._client = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._client.ping()
            logger.info("Redis cache connected: %s", self._redis_url)
        except Exception as e:
            logger.warning("Redis unavailable, caching disabled: %s", e)
            self._client = None

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------
    # Cache operations
    # ------------------------------------------------------------------

    async def get(self, tool_name: str, arguments: dict[str, Any]) -> Optional[str]:
        """Get cached result. Returns None on miss or if Redis unavailable."""
        if not self._client or not self._should_cache(tool_name):
            return None

        try:
            key = self._make_key(tool_name, arguments)
            result = await self._client.get(key)
            if result is not None:
                self._hits += 1
                CACHE_OPERATIONS.labels(operation="hit").inc()
                logger.info("CACHE_HIT: %s", tool_name)
                return result
            self._misses += 1
            CACHE_OPERATIONS.labels(operation="miss").inc()
            logger.info("CACHE_MISS: %s", tool_name)
            return None
        except Exception as e:
            logger.warning("Redis get failed: %s", e)
            return None

    async def set(self, tool_name: str, arguments: dict[str, Any], result: str) -> None:
        """Cache a tool result with TTL."""
        if not self._client or not self._should_cache(tool_name):
            return

        try:
            key = self._make_key(tool_name, arguments)
            await self._client.setex(key, self._default_ttl, result)
        except Exception as e:
            logger.warning("Redis set failed: %s", e)

    async def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0

        total_keys = 0
        if self._client:
            try:
                cursor: int | str = 0
                while True:
                    cursor, keys = await self._client.scan(
                        cursor, match=f"{CACHE_PREFIX}*", count=100
                    )
                    total_keys += len(keys)
                    if cursor == 0:
                        break
            except Exception as e:
                logger.warning("Redis scan failed: %s", e)

        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 4),
            "total_keys": total_keys,
        }

    async def clear(self) -> dict[str, int]:
        """Flush all cached results. Returns count of cleared keys."""
        cleared = 0
        if not self._client:
            return {"cleared": 0}

        try:
            cursor: int | str = 0
            keys_to_delete: list[str] = []
            while True:
                cursor, keys = await self._client.scan(
                    cursor, match=f"{CACHE_PREFIX}*", count=100
                )
                keys_to_delete.extend(keys)
                if cursor == 0:
                    break

            if keys_to_delete:
                cleared = await self._client.delete(*keys_to_delete)
                CACHE_OPERATIONS.labels(operation="clear").inc()
        except Exception as e:
            logger.warning("Redis clear failed: %s", e)

        self._hits = 0
        self._misses = 0
        return {"cleared": cleared}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _should_cache(tool_name: str) -> bool:
        """Don't cache health_check results."""
        mcp_name = tool_name.split("__", 1)[-1] if "__" in tool_name else tool_name
        return mcp_name != "health_check"

    @staticmethod
    def _make_key(tool_name: str, arguments: dict[str, Any]) -> str:
        """Create a cache key from tool name and sorted arguments."""
        payload = json.dumps(
            {"tool": tool_name, "args": sorted(arguments.items())},
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
        return f"{CACHE_PREFIX}{tool_name}:{digest}"
