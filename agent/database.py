"""PostgreSQL logging for tool invocations, sessions, and conversations.

Uses SQLAlchemy async engine with asyncpg driver. PostgreSQL being
unavailable is handled gracefully — the agent continues without persistence.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)

MAX_OUTPUT_LENGTH = 5000  # Truncate output_data beyond this


def _to_uuid(session_id: str) -> uuid.UUID:
    """Convert a session ID string to a UUID.

    If the string is already a valid UUID it is used directly,
    otherwise a deterministic UUID5 is generated.
    """
    try:
        return uuid.UUID(session_id)
    except (ValueError, AttributeError):
        return uuid.uuid5(uuid.NAMESPACE_DNS, str(session_id))


_CREATE_TABLE_STMTS = [
    """CREATE TABLE IF NOT EXISTS sessions (
        id UUID PRIMARY KEY,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        last_active TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        message_count INTEGER NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS tool_invocations (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id UUID REFERENCES sessions(id),
        tool_name VARCHAR(255) NOT NULL,
        server_name VARCHAR(255) NOT NULL,
        input_data JSONB,
        output_data JSONB,
        latency_ms DOUBLE PRECISION NOT NULL,
        cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
        status VARCHAR(50) NOT NULL DEFAULT 'success',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS conversations (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        session_id UUID REFERENCES sessions(id),
        role VARCHAR(50) NOT NULL,
        content TEXT NOT NULL,
        tools_used JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_tool_invocations_session ON tool_invocations(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_tool_invocations_created ON tool_invocations(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_session ON conversations(session_id)",
]


class Database:
    """Async PostgreSQL client for logging and analytics."""

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._engine: Optional[AsyncEngine] = None

    @property
    def available(self) -> bool:
        """Whether the PostgreSQL connection is active."""
        return self._engine is not None

    async def init(self) -> None:
        """Create engine, connection pool, and tables.

        Non-fatal if PostgreSQL is unavailable.
        """
        try:
            self._engine = create_async_engine(self._url, pool_size=5, max_overflow=10)
            async with self._engine.begin() as conn:
                for stmt in _CREATE_TABLE_STMTS:
                    await conn.execute(text(stmt))
            logger.info("PostgreSQL connected — tables ready")
        except Exception as e:
            logger.warning("PostgreSQL unavailable, logging disabled: %s", e)
            self._engine = None

    async def close(self) -> None:
        """Dispose of the engine and connection pool."""
        if self._engine:
            await self._engine.dispose()
            self._engine = None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    async def log_tool_invocation(
        self,
        session_id: str,
        tool_name: str,
        server_name: str,
        input_data: dict[str, Any],
        output_data: str,
        latency_ms: float,
        cache_hit: bool,
        status: str,
    ) -> None:
        """Log a tool invocation to PostgreSQL."""
        if not self._engine:
            return

        try:
            truncated = output_data[:MAX_OUTPUT_LENGTH] if output_data else ""
            try:
                output_json = json.loads(truncated)
            except (json.JSONDecodeError, TypeError):
                output_json = {"raw": truncated}

            sid = _to_uuid(session_id)

            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO sessions (id, created_at, last_active, message_count) "
                        "VALUES (:id, NOW(), NOW(), 0) "
                        "ON CONFLICT (id) DO UPDATE SET last_active = NOW()"
                    ),
                    {"id": sid},
                )
                await conn.execute(
                    text(
                        "INSERT INTO tool_invocations "
                        "(id, session_id, tool_name, server_name, "
                        "input_data, output_data, latency_ms, "
                        "cache_hit, status, created_at) "
                        "VALUES (:id, :sid, :tool, :server, "
                        ":input, :output, :latency, "
                        ":cache_hit, :status, NOW())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "sid": sid,
                        "tool": tool_name,
                        "server": server_name,
                        "input": json.dumps(input_data),
                        "output": json.dumps(output_json),
                        "latency": round(latency_ms, 2),
                        "cache_hit": cache_hit,
                        "status": status,
                    },
                )
        except Exception as e:
            logger.warning("Failed to log tool invocation: %s", e)

    async def log_conversation(
        self,
        session_id: str,
        role: str,
        content: str,
        tools_used: list[str] | None = None,
    ) -> None:
        """Log a conversation turn to PostgreSQL."""
        if not self._engine:
            return

        try:
            sid = _to_uuid(session_id)

            async with self._engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO sessions (id, created_at, last_active, message_count) "
                        "VALUES (:id, NOW(), NOW(), 1) "
                        "ON CONFLICT (id) DO UPDATE "
                        "SET last_active = NOW(), "
                        "message_count = sessions.message_count + 1"
                    ),
                    {"id": sid},
                )
                await conn.execute(
                    text(
                        "INSERT INTO conversations "
                        "(id, session_id, role, content, tools_used, created_at) "
                        "VALUES (:id, :sid, :role, :content, :tools, NOW())"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "sid": sid,
                        "role": role,
                        "content": content,
                        "tools": json.dumps(tools_used or []),
                    },
                )
        except Exception as e:
            logger.warning("Failed to log conversation: %s", e)

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    async def get_tool_analytics(self) -> list[dict[str, Any]]:
        """Aggregated stats per tool: count, avg_latency, success_rate, cache_hit_rate."""
        if not self._engine:
            return []

        try:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT tool_name, server_name, "
                        "COUNT(*) AS total_calls, "
                        "ROUND(AVG(latency_ms)::numeric, 2) AS avg_latency_ms, "
                        "ROUND((SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)::numeric "
                        "/ NULLIF(COUNT(*), 0)::numeric), 4) AS success_rate, "
                        "ROUND((SUM(CASE WHEN cache_hit THEN 1 ELSE 0 END)::numeric "
                        "/ NULLIF(COUNT(*), 0)::numeric), 4) AS cache_hit_rate "
                        "FROM tool_invocations "
                        "GROUP BY tool_name, server_name "
                        "ORDER BY total_calls DESC"
                    )
                )
                return [
                    {
                        "tool_name": row[0],
                        "server_name": row[1],
                        "total_calls": row[2],
                        "avg_latency_ms": float(row[3]),
                        "success_rate": float(row[4]),
                        "cache_hit_rate": float(row[5]),
                    }
                    for row in result.fetchall()
                ]
        except Exception as e:
            logger.warning("Failed to get tool analytics: %s", e)
            return []

    async def get_session_analytics(self) -> dict[str, Any]:
        """Total sessions, avg messages per session, active in last hour."""
        default = {
            "total_sessions": 0,
            "avg_messages_per_session": 0.0,
            "active_last_hour": 0,
        }
        if not self._engine:
            return default

        try:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT "
                        "COUNT(*) AS total_sessions, "
                        "COALESCE(ROUND(AVG(message_count)::numeric, 2), 0) AS avg_messages, "
                        "COALESCE(SUM(CASE WHEN last_active >= NOW() - INTERVAL '1 hour' "
                        "THEN 1 ELSE 0 END), 0) AS active_last_hour "
                        "FROM sessions"
                    )
                )
                row = result.fetchone()
                if not row:
                    return default
                return {
                    "total_sessions": row[0],
                    "avg_messages_per_session": float(row[1]),
                    "active_last_hour": row[2],
                }
        except Exception as e:
            logger.warning("Failed to get session analytics: %s", e)
            return default

    async def get_recent_invocations(self, limit: int = 20) -> list[dict[str, Any]]:
        """Last N tool invocations with details."""
        if not self._engine:
            return []

        try:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT id, session_id, tool_name, server_name, "
                        "input_data, output_data, latency_ms, "
                        "cache_hit, status, created_at "
                        "FROM tool_invocations "
                        "ORDER BY created_at DESC "
                        "LIMIT :limit"
                    ),
                    {"limit": limit},
                )
                return [
                    {
                        "id": str(row[0]),
                        "session_id": str(row[1]),
                        "tool_name": row[2],
                        "server_name": row[3],
                        "input_data": row[4],
                        "output_data": row[5],
                        "latency_ms": row[6],
                        "cache_hit": row[7],
                        "status": row[8],
                        "created_at": row[9].isoformat() if row[9] else None,
                    }
                    for row in result.fetchall()
                ]
        except Exception as e:
            logger.warning("Failed to get recent invocations: %s", e)
            return []
