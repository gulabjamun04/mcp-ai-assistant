"""Unit tests for agent.database — PostgreSQL logging and analytics."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import database
from agent.database import MAX_OUTPUT_LENGTH, Database, _to_uuid

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db():
    """Create a Database with a mocked async engine.

    Returns (db, mock_conn) where mock_conn receives all execute() calls.
    """
    db = Database("postgresql+asyncpg://test:test@localhost/test")

    mock_conn = AsyncMock()

    # engine.begin() → async context manager → connection
    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)

    # engine.connect() → async context manager → connection
    connect_ctx = AsyncMock()
    connect_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    connect_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=begin_ctx)
    mock_engine.connect = MagicMock(return_value=connect_ctx)
    mock_engine.dispose = AsyncMock()

    db._engine = mock_engine
    return db, mock_conn


# ---------------------------------------------------------------------------
# _to_uuid helper
# ---------------------------------------------------------------------------


class TestToUuid:
    def test_valid_uuid_string(self):
        """A valid UUID string is returned as-is."""
        uid = str(uuid.uuid4())
        assert _to_uuid(uid) == uuid.UUID(uid)

    def test_non_uuid_string_deterministic(self):
        """Non-UUID strings produce deterministic UUID5 values."""
        result1 = _to_uuid("my-session")
        result2 = _to_uuid("my-session")
        assert result1 == result2
        assert isinstance(result1, uuid.UUID)

    def test_different_strings_different_uuids(self):
        """Different input strings produce different UUIDs."""
        assert _to_uuid("session-a") != _to_uuid("session-b")

    def test_empty_string(self):
        """Empty string still produces a valid UUID."""
        result = _to_uuid("")
        assert isinstance(result, uuid.UUID)


# ---------------------------------------------------------------------------
# Init / close
# ---------------------------------------------------------------------------


class TestInit:
    @pytest.mark.asyncio
    async def test_init_success(self):
        """Successful init creates engine and runs CREATE TABLE."""
        db = Database("postgresql+asyncpg://test@localhost/test")

        mock_conn = AsyncMock()
        begin_ctx = AsyncMock()
        begin_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        begin_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(return_value=begin_ctx)

        with patch("agent.database.create_async_engine", return_value=mock_engine):
            await db.init()

        assert db.available is True
        assert mock_conn.execute.await_count == len(database._CREATE_TABLE_STMTS)

    @pytest.mark.asyncio
    async def test_init_failure_sets_none(self):
        """Failed init sets engine to None (graceful degradation)."""
        db = Database("postgresql+asyncpg://test@localhost/test")

        with patch(
            "agent.database.create_async_engine",
            side_effect=ConnectionError("refused"),
        ):
            await db.init()

        assert db.available is False

    @pytest.mark.asyncio
    async def test_close(self):
        """Close disposes of the engine."""
        db, _ = _make_db()
        assert db.available is True

        await db.close()

        assert db.available is False
        assert db._engine is None

    @pytest.mark.asyncio
    async def test_close_noop_when_no_engine(self):
        """Close does nothing when engine is already None."""
        db = Database("postgresql+asyncpg://test@localhost/test")
        await db.close()  # should not raise


# ---------------------------------------------------------------------------
# log_tool_invocation
# ---------------------------------------------------------------------------


class TestLogToolInvocation:
    @pytest.mark.asyncio
    async def test_logs_successful_invocation(self):
        """Inserts a row into tool_invocations."""
        db, mock_conn = _make_db()

        await db.log_tool_invocation(
            session_id="abc123",
            tool_name="web_search__search",
            server_name="web_search",
            input_data={"query": "test"},
            output_data='{"results": []}',
            latency_ms=150.5,
            cache_hit=False,
            status="success",
        )

        # Two execute calls: upsert session + insert invocation
        assert mock_conn.execute.await_count == 2
        calls = mock_conn.execute.call_args_list

        # First call: session upsert
        sql_0 = str(calls[0][0][0])
        assert "INSERT INTO sessions" in sql_0
        assert "ON CONFLICT" in sql_0

        # Second call: tool_invocations insert
        sql_1 = str(calls[1][0][0])
        assert "INSERT INTO tool_invocations" in sql_1
        params = calls[1][0][1]
        assert params["tool"] == "web_search__search"
        assert params["server"] == "web_search"
        assert params["cache_hit"] is False
        assert params["status"] == "success"
        assert params["latency"] == 150.5

    @pytest.mark.asyncio
    async def test_truncates_output(self):
        """Output data is truncated to MAX_OUTPUT_LENGTH."""
        db, mock_conn = _make_db()
        long_output = "x" * (MAX_OUTPUT_LENGTH + 1000)

        await db.log_tool_invocation(
            session_id="s1",
            tool_name="tool",
            server_name="srv",
            input_data={},
            output_data=long_output,
            latency_ms=10.0,
            cache_hit=False,
            status="success",
        )

        # The output should be wrapped in {"raw": ...} and truncated
        calls = mock_conn.execute.call_args_list
        params = calls[1][0][1]
        output_json = json.loads(params["output"])
        assert len(output_json["raw"]) == MAX_OUTPUT_LENGTH

    @pytest.mark.asyncio
    async def test_parses_json_output(self):
        """Valid JSON output is stored as parsed JSON."""
        db, mock_conn = _make_db()

        await db.log_tool_invocation(
            session_id="s1",
            tool_name="tool",
            server_name="srv",
            input_data={},
            output_data='{"key": "value"}',
            latency_ms=10.0,
            cache_hit=False,
            status="success",
        )

        calls = mock_conn.execute.call_args_list
        params = calls[1][0][1]
        output_json = json.loads(params["output"])
        assert output_json == {"key": "value"}

    @pytest.mark.asyncio
    async def test_noop_when_unavailable(self):
        """Does nothing when engine is None."""
        db = Database("postgresql+asyncpg://test@localhost/test")
        # Should not raise
        await db.log_tool_invocation(
            session_id="s1",
            tool_name="tool",
            server_name="srv",
            input_data={},
            output_data="",
            latency_ms=0,
            cache_hit=False,
            status="success",
        )

    @pytest.mark.asyncio
    async def test_handles_db_error(self):
        """Logs warning on database error, does not raise."""
        db, mock_conn = _make_db()
        mock_conn.execute = AsyncMock(side_effect=ConnectionError("lost"))

        # Should not raise
        await db.log_tool_invocation(
            session_id="s1",
            tool_name="tool",
            server_name="srv",
            input_data={},
            output_data="{}",
            latency_ms=10.0,
            cache_hit=False,
            status="success",
        )


# ---------------------------------------------------------------------------
# log_conversation
# ---------------------------------------------------------------------------


class TestLogConversation:
    @pytest.mark.asyncio
    async def test_logs_user_message(self):
        """Inserts a user conversation row."""
        db, mock_conn = _make_db()

        await db.log_conversation(
            session_id="s1",
            role="user",
            content="Hello, world!",
        )

        assert mock_conn.execute.await_count == 2
        calls = mock_conn.execute.call_args_list

        # Session upsert increments message_count
        sql_0 = str(calls[0][0][0])
        assert "message_count" in sql_0

        # Conversation insert
        sql_1 = str(calls[1][0][0])
        assert "INSERT INTO conversations" in sql_1
        params = calls[1][0][1]
        assert params["role"] == "user"
        assert params["content"] == "Hello, world!"

    @pytest.mark.asyncio
    async def test_logs_assistant_with_tools(self):
        """Logs assistant response with tools_used list."""
        db, mock_conn = _make_db()

        await db.log_conversation(
            session_id="s1",
            role="assistant",
            content="Here are the results.",
            tools_used=["web_search__search", "note_manager__save_note"],
        )

        calls = mock_conn.execute.call_args_list
        params = calls[1][0][1]
        assert params["role"] == "assistant"
        tools = json.loads(params["tools"])
        assert "web_search__search" in tools
        assert "note_manager__save_note" in tools

    @pytest.mark.asyncio
    async def test_empty_tools_defaults_to_empty_list(self):
        """tools_used=None is stored as an empty JSON list."""
        db, mock_conn = _make_db()

        await db.log_conversation(
            session_id="s1",
            role="user",
            content="Hi",
        )

        calls = mock_conn.execute.call_args_list
        params = calls[1][0][1]
        assert json.loads(params["tools"]) == []

    @pytest.mark.asyncio
    async def test_noop_when_unavailable(self):
        """Does nothing when engine is None."""
        db = Database("postgresql+asyncpg://test@localhost/test")
        await db.log_conversation(session_id="s1", role="user", content="Hi")

    @pytest.mark.asyncio
    async def test_handles_db_error(self):
        """Logs warning on database error, does not raise."""
        db, mock_conn = _make_db()
        mock_conn.execute = AsyncMock(side_effect=ConnectionError("lost"))

        await db.log_conversation(session_id="s1", role="user", content="Hi")


# ---------------------------------------------------------------------------
# get_tool_analytics
# ---------------------------------------------------------------------------


class TestToolAnalytics:
    @pytest.mark.asyncio
    async def test_returns_aggregated_stats(self):
        """Returns per-tool statistics from the query."""
        db, mock_conn = _make_db()

        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(
            return_value=[
                ("web_search__search", "web_search", 42, 125.50, 0.9524, 0.3333),
                ("note_manager__save_note", "note_manager", 10, 50.00, 1.0, 0.0),
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_result)

        stats = await db.get_tool_analytics()

        assert len(stats) == 2
        assert stats[0]["tool_name"] == "web_search__search"
        assert stats[0]["total_calls"] == 42
        assert stats[0]["avg_latency_ms"] == 125.50
        assert stats[0]["success_rate"] == 0.9524
        assert stats[0]["cache_hit_rate"] == 0.3333
        assert stats[1]["tool_name"] == "note_manager__save_note"

    @pytest.mark.asyncio
    async def test_empty_when_unavailable(self):
        """Returns empty list when engine is None."""
        db = Database("postgresql+asyncpg://test@localhost/test")
        result = await db.get_tool_analytics()
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_db_error(self):
        """Returns empty list on database error."""
        db, mock_conn = _make_db()
        mock_conn.execute = AsyncMock(side_effect=ConnectionError("lost"))

        result = await db.get_tool_analytics()
        assert result == []


# ---------------------------------------------------------------------------
# get_session_analytics
# ---------------------------------------------------------------------------


class TestSessionAnalytics:
    @pytest.mark.asyncio
    async def test_returns_session_stats(self):
        """Returns session statistics from the query."""
        db, mock_conn = _make_db()

        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=(15, 4.5, 3))
        mock_conn.execute = AsyncMock(return_value=mock_result)

        stats = await db.get_session_analytics()

        assert stats == {
            "total_sessions": 15,
            "avg_messages_per_session": 4.5,
            "active_last_hour": 3,
        }

    @pytest.mark.asyncio
    async def test_defaults_when_unavailable(self):
        """Returns defaults when engine is None."""
        db = Database("postgresql+asyncpg://test@localhost/test")
        stats = await db.get_session_analytics()
        assert stats == {
            "total_sessions": 0,
            "avg_messages_per_session": 0.0,
            "active_last_hour": 0,
        }

    @pytest.mark.asyncio
    async def test_defaults_when_no_rows(self):
        """Returns defaults when query returns no rows."""
        db, mock_conn = _make_db()
        mock_result = MagicMock()
        mock_result.fetchone = MagicMock(return_value=None)
        mock_conn.execute = AsyncMock(return_value=mock_result)

        stats = await db.get_session_analytics()
        assert stats["total_sessions"] == 0

    @pytest.mark.asyncio
    async def test_handles_db_error(self):
        """Returns defaults on database error."""
        db, mock_conn = _make_db()
        mock_conn.execute = AsyncMock(side_effect=ConnectionError("lost"))

        stats = await db.get_session_analytics()
        assert stats["total_sessions"] == 0


# ---------------------------------------------------------------------------
# get_recent_invocations
# ---------------------------------------------------------------------------


class TestRecentInvocations:
    @pytest.mark.asyncio
    async def test_returns_recent_entries(self):
        """Returns recent tool invocations."""
        db, mock_conn = _make_db()

        from datetime import UTC, datetime

        now = datetime.now(UTC)
        uid = uuid.uuid4()
        sid = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(
            return_value=[
                (
                    uid,
                    sid,
                    "web_search__search",
                    "web_search",
                    {"query": "test"},
                    {"results": []},
                    120.5,
                    False,
                    "success",
                    now,
                ),
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_result)

        recent = await db.get_recent_invocations(limit=10)

        assert len(recent) == 1
        assert recent[0]["tool_name"] == "web_search__search"
        assert recent[0]["id"] == str(uid)
        assert recent[0]["session_id"] == str(sid)
        assert recent[0]["latency_ms"] == 120.5
        assert recent[0]["cache_hit"] is False
        assert recent[0]["status"] == "success"
        assert recent[0]["created_at"] == now.isoformat()

    @pytest.mark.asyncio
    async def test_handles_null_timestamp(self):
        """Handles None created_at gracefully."""
        db, mock_conn = _make_db()

        uid = uuid.uuid4()
        sid = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(
            return_value=[
                (
                    uid,
                    sid,
                    "tool",
                    "srv",
                    {},
                    {},
                    10.0,
                    False,
                    "success",
                    None,
                ),
            ]
        )
        mock_conn.execute = AsyncMock(return_value=mock_result)

        recent = await db.get_recent_invocations()
        assert recent[0]["created_at"] is None

    @pytest.mark.asyncio
    async def test_empty_when_unavailable(self):
        """Returns empty list when engine is None."""
        db = Database("postgresql+asyncpg://test@localhost/test")
        result = await db.get_recent_invocations()
        assert result == []

    @pytest.mark.asyncio
    async def test_handles_db_error(self):
        """Returns empty list on database error."""
        db, mock_conn = _make_db()
        mock_conn.execute = AsyncMock(side_effect=ConnectionError("lost"))

        result = await db.get_recent_invocations()
        assert result == []

    @pytest.mark.asyncio
    async def test_passes_limit_parameter(self):
        """The limit is passed to the SQL query."""
        db, mock_conn = _make_db()

        mock_result = MagicMock()
        mock_result.fetchall = MagicMock(return_value=[])
        mock_conn.execute = AsyncMock(return_value=mock_result)

        await db.get_recent_invocations(limit=5)

        call_args = mock_conn.execute.call_args
        params = call_args[0][1]
        assert params["limit"] == 5
