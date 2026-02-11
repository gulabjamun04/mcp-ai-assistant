"""
Note Manager MCP Server

Exposes tools for saving, retrieving, and searching notes via the
Model Context Protocol.  Runs on port 8001 with SSE transport.
"""

import logging
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from .storage import NoteStorage

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("note_manager")

# ---------------------------------------------------------------------------
# MCP server + storage
# ---------------------------------------------------------------------------
mcp = FastMCP("note-manager", host="0.0.0.0", port=8001)
storage = NoteStorage()

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def save_note(title: str, content: str, tags: list[str] | None = None) -> dict:
    """Save a new note with a title, content, and optional tags.

    Use this tool when the user wants to create, store, or remember a piece
    of information for later retrieval.

    Args:
        title: Short descriptive title for the note.
        content: The full body / text of the note.
        tags: Optional list of tags for categorisation.

    Returns:
        Dictionary with the generated note_id and a confirmation message.
    """
    tags = tags or []
    note = storage.save(title=title, content=content, tags=tags)
    logger.info("Tool save_note invoked — id=%s", note.id)
    return {
        "note_id": note.id,
        "message": f"Note '{note.title}' saved successfully.",
    }


@mcp.tool()
def get_notes(tag: str | None = None) -> dict:
    """Retrieve stored notes, optionally filtered by tag.

    Use this tool when the user wants to list or browse their saved notes.
    If a tag is provided, only notes with that tag are returned.

    Args:
        tag: Optional tag to filter notes by (case-insensitive).

    Returns:
        Dictionary with a list of matching notes and their count.
    """
    if tag:
        notes = storage.get_by_tag(tag)
        logger.info("Tool get_notes invoked — tag='%s', found=%d", tag, len(notes))
    else:
        notes = storage.get_all()
        logger.info("Tool get_notes invoked — all, found=%d", len(notes))

    return {
        "count": len(notes),
        "notes": [n.model_dump() for n in notes],
    }


@mcp.tool()
def search_notes(query: str) -> dict:
    """Search notes by keyword (substring match on title and content).

    Use this tool when the user wants to find notes related to a specific
    topic or keyword.

    Args:
        query: The search string to match against note titles and content.

    Returns:
        Dictionary with matching notes and their count.
    """
    results = storage.search(query)
    logger.info("Tool search_notes invoked — query='%s', found=%d", query, len(results))
    return {
        "count": len(results),
        "notes": [n.model_dump() for n in results],
    }


@mcp.tool()
def health_check() -> dict:
    """Check whether the Note Manager server is healthy.

    Use this tool to verify the server is running and responsive.

    Returns:
        Dictionary with server status, note count, and timestamp.
    """
    logger.info("Tool health_check invoked")
    return {
        "status": "healthy",
        "server": "note-manager",
        "total_notes": storage.count,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Note Manager MCP server on port 8001 ...")
    mcp.run(transport="sse")
