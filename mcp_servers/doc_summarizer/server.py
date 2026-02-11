"""
Document Summarizer MCP Server

Exposes tools for summarizing text and extracting key points using a local
Ollama instance.  Runs on port 8003 with SSE transport.
"""

import logging
import os
import re
from datetime import UTC, datetime

import httpx
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("doc_summarizer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_GENERATE_URL = f"{OLLAMA_BASE_URL}/api/generate"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:1.7b")
OLLAMA_TIMEOUT = 120  # seconds — Qwen3 on CPU can be slow for summarization
MAX_TEXT_LENGTH = 10_000

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP("doc-summarizer", host="0.0.0.0", port=8003)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_text(text: str) -> str | None:
    """Return an error message if *text* is invalid, else None."""
    if not text or not text.strip():
        return "Text is empty or whitespace-only."
    if len(text) > MAX_TEXT_LENGTH:
        return f"Text too long ({len(text)} chars). Maximum is {MAX_TEXT_LENGTH}."
    return None


def _strip_thinking_tags(text: str) -> str:
    """Remove ``<think>...</think>`` blocks that Qwen3 may produce."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def _parse_key_points(text: str, expected: int) -> list[str]:
    """Parse a numbered or bulleted list from LLM output.

    Falls back to sentence splitting if no list structure is detected.
    """
    # Try numbered lines: "1. ...", "1) ..."
    numbered = re.findall(r"^\s*\d+[\.\)]\s*(.+)", text, re.MULTILINE)
    if numbered:
        return [p.strip() for p in numbered[:expected]]

    # Try bullet lines: "- ...", "* ..."
    bulleted = re.findall(r"^\s*[\-\*\u2022]\s*(.+)", text, re.MULTILINE)
    if bulleted:
        return [p.strip() for p in bulleted[:expected]]

    # Fallback: split on sentence-ending punctuation
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in sentences if s.strip()][:expected]


async def _call_ollama(prompt: str) -> dict:
    """Send a prompt to the local Ollama instance and return the response.

    Returns:
        ``{"response": str}`` on success, or ``{"error": str}`` on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=OLLAMA_TIMEOUT) as client:
            resp = await client.post(
                OLLAMA_GENERATE_URL,
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            )
            resp.raise_for_status()
            data = resp.json()
            raw_text = data.get("response", "")
            cleaned = _strip_thinking_tags(raw_text)
            return {"response": cleaned}
    except httpx.ConnectError:
        logger.error("Cannot connect to Ollama at %s", OLLAMA_BASE_URL)
        return {
            "error": f"Cannot connect to Ollama at {OLLAMA_BASE_URL}. Is it running?"
        }
    except httpx.TimeoutException:
        logger.error("Ollama request timed out after %ds", OLLAMA_TIMEOUT)
        return {"error": f"Ollama request timed out after {OLLAMA_TIMEOUT}s."}
    except Exception as exc:
        logger.error("Ollama request failed: %s", exc)
        return {"error": f"Ollama request failed: {exc}"}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
async def summarize_text(text: str, max_length: int = 200) -> dict:
    """Summarize the given text using a local LLM (Ollama).

    Use this tool when the user wants a concise summary of a piece of text.

    Args:
        text: The text to summarize (max 10 000 characters).
        max_length: Desired maximum character length of the summary (50-1000,
            default 200).

    Returns:
        Dictionary with text_length, summary, and summary_length.
    """
    logger.info(
        "Tool summarize_text invoked — text_length=%d, max_length=%d",
        len(text),
        max_length,
    )

    error = _validate_text(text)
    if error:
        return {
            "text_length": len(text),
            "summary": "",
            "summary_length": 0,
            "error": error,
        }

    max_length = max(50, min(max_length, 1000))

    prompt = (
        f"Summarize the following text in a concise, factual manner. "
        f"Keep the summary under {max_length} characters. "
        f"Do not add information not present in the text. "
        f"Respond with only the summary, no preamble.\n\n"
        f"TEXT:\n{text}"
    )

    result = await _call_ollama(prompt)
    if "error" in result:
        return {
            "text_length": len(text),
            "summary": "",
            "summary_length": 0,
            "error": result["error"],
        }

    summary = result["response"]
    return {
        "text_length": len(text),
        "summary": summary,
        "summary_length": len(summary),
    }


@mcp.tool()
async def extract_key_points(text: str, num_points: int = 5) -> dict:
    """Extract key points from the given text using a local LLM.

    Use this tool when the user wants the main ideas from a piece of text
    presented as a list of bullet points.

    Args:
        text: The text to analyse (max 10 000 characters).
        num_points: Number of key points to extract (1-15, default 5).

    Returns:
        Dictionary with text_length, key_points list, and count.
    """
    logger.info(
        "Tool extract_key_points invoked — text_length=%d, num_points=%d",
        len(text),
        num_points,
    )

    error = _validate_text(text)
    if error:
        return {"text_length": len(text), "key_points": [], "count": 0, "error": error}

    num_points = max(1, min(num_points, 15))

    prompt = (
        f"Extract exactly {num_points} key points from the following text. "
        f"Present them as a numbered list (1. ... 2. ... etc). "
        f"Each point should be a concise sentence. "
        f"Respond with only the numbered list, no preamble.\n\n"
        f"TEXT:\n{text}"
    )

    result = await _call_ollama(prompt)
    if "error" in result:
        return {
            "text_length": len(text),
            "key_points": [],
            "count": 0,
            "error": result["error"],
        }

    points = _parse_key_points(result["response"], num_points)
    return {
        "text_length": len(text),
        "key_points": points,
        "count": len(points),
    }


@mcp.tool()
async def health_check() -> dict:
    """Check whether the Doc Summarizer server and Ollama are healthy.

    Use this tool to verify the server is running, and that Ollama is
    reachable with the expected model available.

    Returns:
        Dictionary with server and Ollama status information.
    """
    logger.info("Tool health_check invoked")

    ollama_status = "unreachable"
    ollama_model = None

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if resp.status_code == 200:
                models = resp.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                if any(OLLAMA_MODEL in name for name in model_names):
                    ollama_status = "available"
                    ollama_model = OLLAMA_MODEL
                else:
                    ollama_status = "running_but_model_missing"
    except Exception:
        pass

    status = "healthy" if ollama_status == "available" else "degraded"

    return {
        "status": status,
        "server": "doc-summarizer",
        "ollama_status": ollama_status,
        "ollama_model": ollama_model,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Doc Summarizer MCP server on port 8003 ...")
    mcp.run(transport="sse")
