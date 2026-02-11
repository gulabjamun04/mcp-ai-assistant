# Web Search MCP Server

MCP tool server for searching the web via DuckDuckGo and fetching web page content.

## Tools

| Tool | Description |
|------|-------------|
| `web_search` | Search the web via DuckDuckGo, returns top results (title, url, snippet) |
| `fetch_url` | Fetch a URL and return cleaned visible text (max 5 000 chars) |
| `health_check` | Check server status |

## Features

- **DuckDuckGo search** — no API key required
- **HTML cleaning** — strips scripts, styles, nav, footer, header, aside
- **Rate limiting** — max 10 searches per minute (in-memory)
- **URL validation** — rejects non-HTTP(S) schemes
- **Timeout handling** — 10 s max per outbound request

## Run

```bash
# From project root, with venv activated
python -m mcp_servers.web_search.server
```

Server starts on `http://localhost:8002` using SSE transport.

## Test

```bash
# Unit tests only (no server needed)
pytest tests/test_web_search.py -v -k "not MCPIntegration"

# Full suite including integration tests (starts server automatically)
pytest tests/test_web_search.py -v
```
