# Document Summarizer MCP Server

MCP tool server for summarizing text and extracting key points using a local Ollama instance.

## Tools

| Tool | Description |
|------|-------------|
| `summarize_text` | Summarize text into a concise paragraph (configurable max length) |
| `extract_key_points` | Extract N key points as a numbered list |
| `health_check` | Check server and Ollama status |

## Features

- **Local LLM inference** — uses Ollama, no API keys required
- **Thinking tag cleanup** — strips `<think>...</think>` blocks from Qwen3 output
- **Input validation** — rejects empty text and text over 10 000 characters
- **Configurable output** — adjustable summary length (50-1000 chars) and key point count (1-15)
- **Graceful error handling** — returns structured errors for Ollama timeouts and connection failures

## Prerequisites

- [Ollama](https://ollama.ai) installed and running on port 11434
- Qwen3 model pulled: `ollama pull qwen3:1.7b`

## Run

```bash
# From project root, with venv activated
python -m mcp_servers.doc_summarizer.server
```

Server starts on `http://localhost:8003` using SSE transport.

## Test

```bash
# Unit tests only (no Ollama needed)
pytest tests/test_doc_summarizer.py -v -k "not MCPIntegration"

# Full suite including integration tests (requires Ollama + qwen3:1.7b)
pytest tests/test_doc_summarizer.py -v
```
