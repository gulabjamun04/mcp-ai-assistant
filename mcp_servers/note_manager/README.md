# Note Manager MCP Server

MCP tool server for saving, retrieving, and searching notes. Stores data in a local JSON file.

## Tools

| Tool | Description |
|------|-------------|
| `save_note` | Save a new note with title, content, and optional tags |
| `get_notes` | List all notes or filter by tag |
| `search_notes` | Search notes by keyword (substring match) |
| `health_check` | Check server status |

## Run

```bash
# From project root, with venv activated
python -m mcp_servers.note_manager.server
```

Server starts on `http://localhost:8001` using SSE transport.

## Test

```bash
pytest tests/test_note_manager.py -v
```

## Data

Notes are persisted in `mcp_servers/note_manager/notes_data.json`. This file is created automatically on first run.
