"""Pydantic models for the Note Manager MCP server."""

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


class Note(BaseModel):
    """A single note with metadata."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str = Field(..., min_length=1, max_length=200, description="Note title")
    content: str = Field(..., min_length=1, description="Note content")
    tags: list[str] = Field(default_factory=list, description="List of tags")
    created_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO-8601 creation timestamp",
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description="ISO-8601 last update timestamp",
    )


class NoteStore(BaseModel):
    """Container for all notes, used for JSON serialization."""

    notes: list[Note] = Field(default_factory=list)
