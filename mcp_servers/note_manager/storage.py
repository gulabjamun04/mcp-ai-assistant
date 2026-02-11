"""JSON file-based storage layer for the Note Manager."""

import json
import logging
from pathlib import Path

from .models import Note, NoteStore

logger = logging.getLogger("note_manager.storage")

DEFAULT_STORAGE_PATH = Path(__file__).parent / "notes_data.json"


class NoteStorage:
    """Manages note persistence using a local JSON file."""

    def __init__(self, storage_path: Path = DEFAULT_STORAGE_PATH) -> None:
        self._path = storage_path
        self._store = NoteStore()
        self._load()

    def _load(self) -> None:
        """Load notes from disk. Creates file if missing."""
        if self._path.exists():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._store = NoteStore.model_validate(raw)
                logger.info(
                    "Loaded %d notes from %s", len(self._store.notes), self._path
                )
            except (json.JSONDecodeError, Exception) as exc:
                logger.error("Failed to load notes: %s — starting fresh", exc)
                self._store = NoteStore()
        else:
            logger.info("No storage file found at %s — starting fresh", self._path)
            self._persist()

    def _persist(self) -> None:
        """Write current state to disk."""
        self._path.write_text(
            self._store.model_dump_json(indent=2),
            encoding="utf-8",
        )

    def save(self, title: str, content: str, tags: list[str]) -> Note:
        """Create and persist a new note."""
        note = Note(title=title, content=content, tags=tags)
        self._store.notes.append(note)
        self._persist()
        logger.info("Saved note %s — '%s'", note.id, note.title)
        return note

    def get_all(self) -> list[Note]:
        """Return every stored note."""
        return list(self._store.notes)

    def get_by_tag(self, tag: str) -> list[Note]:
        """Return notes that contain the given tag (case-insensitive)."""
        tag_lower = tag.lower()
        return [
            n for n in self._store.notes if tag_lower in [t.lower() for t in n.tags]
        ]

    def search(self, query: str) -> list[Note]:
        """Return notes whose title or content contains the query (case-insensitive)."""
        q = query.lower()
        return [
            n
            for n in self._store.notes
            if q in n.title.lower() or q in n.content.lower()
        ]

    @property
    def count(self) -> int:
        """Number of stored notes."""
        return len(self._store.notes)
