"""Tests for the Note Manager MCP server.

Unit tests for models/storage, plus integration tests that start the
MCP server as a subprocess and exercise every tool via the MCP client SDK.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession
from mcp.client.sse import sse_client

from mcp_servers.note_manager.models import Note, NoteStore
from mcp_servers.note_manager.storage import NoteStorage

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SERVER_MODULE = "mcp_servers.note_manager.server"
DATA_FILE = PROJECT_ROOT / "mcp_servers" / "note_manager" / "notes_data.json"
SERVER_URL = "http://localhost:8001/sse"


# ===================================================================
# UNIT TESTS — models & storage
# ===================================================================


@pytest.fixture()
def tmp_storage(tmp_path: Path) -> NoteStorage:
    """Return a NoteStorage backed by a temp JSON file."""
    return NoteStorage(storage_path=tmp_path / "test_notes.json")


class TestNoteModel:
    def test_create_note_defaults(self) -> None:
        note = Note(title="Hello", content="World")
        assert note.id
        assert note.title == "Hello"
        assert note.content == "World"
        assert note.tags == []
        assert note.created_at
        assert note.updated_at

    def test_create_note_with_tags(self) -> None:
        note = Note(title="T", content="C", tags=["a", "b"])
        assert note.tags == ["a", "b"]

    def test_title_min_length(self) -> None:
        with pytest.raises(Exception):
            Note(title="", content="body")

    def test_content_min_length(self) -> None:
        with pytest.raises(Exception):
            Note(title="ok", content="")


class TestNoteStore:
    def test_empty_store(self) -> None:
        store = NoteStore()
        assert store.notes == []

    def test_serialization_roundtrip(self) -> None:
        note = Note(title="T", content="C", tags=["x"])
        store = NoteStore(notes=[note])
        raw = store.model_dump_json()
        restored = NoteStore.model_validate_json(raw)
        assert len(restored.notes) == 1
        assert restored.notes[0].title == "T"


class TestNoteStorage:
    def test_save_creates_note(self, tmp_storage: NoteStorage) -> None:
        note = tmp_storage.save("Title", "Content", ["tag1"])
        assert note.title == "Title"
        assert note.content == "Content"
        assert note.tags == ["tag1"]
        assert tmp_storage.count == 1

    def test_get_all(self, tmp_storage: NoteStorage) -> None:
        tmp_storage.save("A", "aaa", [])
        tmp_storage.save("B", "bbb", [])
        assert len(tmp_storage.get_all()) == 2

    def test_get_by_tag(self, tmp_storage: NoteStorage) -> None:
        tmp_storage.save("A", "aaa", ["python"])
        tmp_storage.save("B", "bbb", ["rust"])
        tmp_storage.save("C", "ccc", ["python", "web"])
        assert len(tmp_storage.get_by_tag("python")) == 2
        assert len(tmp_storage.get_by_tag("rust")) == 1
        assert len(tmp_storage.get_by_tag("go")) == 0

    def test_get_by_tag_case_insensitive(self, tmp_storage: NoteStorage) -> None:
        tmp_storage.save("A", "aaa", ["Python"])
        assert len(tmp_storage.get_by_tag("python")) == 1
        assert len(tmp_storage.get_by_tag("PYTHON")) == 1

    def test_search(self, tmp_storage: NoteStorage) -> None:
        tmp_storage.save("Meeting notes", "Discuss roadmap", [])
        tmp_storage.save("Shopping list", "Buy milk", [])
        assert len(tmp_storage.search("meeting")) == 1
        assert len(tmp_storage.search("milk")) == 1
        assert len(tmp_storage.search("xyz")) == 0

    def test_search_case_insensitive(self, tmp_storage: NoteStorage) -> None:
        tmp_storage.save("Hello World", "content here", [])
        assert len(tmp_storage.search("hello")) == 1
        assert len(tmp_storage.search("WORLD")) == 1

    def test_persistence(self, tmp_path: Path) -> None:
        path = tmp_path / "persist.json"
        s1 = NoteStorage(storage_path=path)
        s1.save("Persist", "This should survive reload", ["test"])
        s2 = NoteStorage(storage_path=path)
        assert s2.count == 1
        assert s2.get_all()[0].title == "Persist"

    def test_empty_file_created(self, tmp_path: Path) -> None:
        path = tmp_path / "new.json"
        assert not path.exists()
        NoteStorage(storage_path=path)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["notes"] == []


# ===================================================================
# INTEGRATION TESTS — MCP client ↔ server
# ===================================================================


def _parse_tool_response(result) -> dict:
    """Extract the JSON dict from a CallToolResult."""
    text = result.content[0].text
    return json.loads(text)


@pytest.fixture(scope="module")
def note_server():
    """Start the Note Manager MCP server, yield, then stop + clean up."""
    # Wipe data file so tests start fresh
    if DATA_FILE.exists():
        DATA_FILE.unlink()

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

    if DATA_FILE.exists():
        DATA_FILE.unlink()


@pytest.mark.integration
class TestMCPIntegration:
    """Integration tests that talk to the live MCP server."""

    # ------ save_note ------

    def test_save_three_notes(self, note_server) -> None:
        """Save 3 notes with different tags and verify each returns an id."""

        async def _run() -> list[dict]:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    results = []
                    for title, content, tags in [
                        ("Python tips", "Use list comprehensions", ["python", "tips"]),
                        ("Grocery list", "Eggs, milk, bread", ["personal"]),
                        ("MCP notes", "FastMCP uses decorators", ["python", "mcp"]),
                    ]:
                        r = await session.call_tool(
                            "save_note",
                            {"title": title, "content": content, "tags": tags},
                        )
                        results.append(_parse_tool_response(r))
                    return results

            return results

        results = anyio.run(_run)
        assert len(results) == 3
        for r in results:
            assert "note_id" in r
            assert "saved successfully" in r["message"]

    # ------ get_notes ------

    def test_get_all_notes(self, note_server) -> None:
        """After saving 3 notes, get_notes() should return all 3."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool("get_notes", {})
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["count"] == 3
        assert len(data["notes"]) == 3

    def test_get_notes_filter_by_tag(self, note_server) -> None:
        """Filter by 'python' tag should return 2 notes."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool("get_notes", {"tag": "python"})
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["count"] == 2

    def test_get_notes_filter_no_match(self, note_server) -> None:
        """Filter by a tag that doesn't exist should return 0."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool("get_notes", {"tag": "nonexistent"})
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["count"] == 0
        assert data["notes"] == []

    # ------ search_notes ------

    def test_search_existing_keyword(self, note_server) -> None:
        """Search for 'comprehensions' should find the Python tips note."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "search_notes", {"query": "comprehensions"}
                    )
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["count"] == 1
        assert "Python tips" in data["notes"][0]["title"]

    def test_search_no_match(self, note_server) -> None:
        """Search for a keyword that doesn't exist should return 0."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "search_notes", {"query": "zzzznotfound"}
                    )
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["count"] == 0
        assert data["notes"] == []

    # ------ health_check ------

    def test_health_check(self, note_server) -> None:
        """health_check should return healthy status with note count."""

        async def _run() -> dict:
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool("health_check", {})
                    return _parse_tool_response(r)

        data = anyio.run(_run)
        assert data["status"] == "healthy"
        assert data["server"] == "note-manager"
        assert data["total_notes"] == 3
        assert "timestamp" in data

    # ------ error cases ------

    def test_save_empty_title_error(self, note_server) -> None:
        """Saving a note with empty title should return an error."""

        async def _run():
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "save_note",
                        {"title": "", "content": "some body", "tags": []},
                    )
                    return r

        result = anyio.run(_run)
        assert (
            result.isError
            or "error" in result.content[0].text.lower()
            or "validation" in result.content[0].text.lower()
        )

    def test_save_empty_content_error(self, note_server) -> None:
        """Saving a note with empty content should return an error."""

        async def _run():
            async with sse_client(SERVER_URL) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    r = await session.call_tool(
                        "save_note",
                        {"title": "Valid title", "content": "", "tags": []},
                    )
                    return r

        result = anyio.run(_run)
        assert (
            result.isError
            or "error" in result.content[0].text.lower()
            or "validation" in result.content[0].text.lower()
        )
