"""
Web Search MCP Server

Exposes tools for searching the web via DuckDuckGo and fetching web page
content.  Runs on port 8002 with SSE transport.
"""

import logging
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("web_search")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_FETCH_CHARS = 5000
REQUEST_TIMEOUT = 10
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60  # seconds

# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
mcp = FastMCP("web-search", host="0.0.0.0", port=8002)

# ---------------------------------------------------------------------------
# Rate limiter (in-memory, per-process)
# ---------------------------------------------------------------------------
_search_timestamps: list[float] = []


def _check_rate_limit() -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.monotonic()
    # Prune timestamps older than the window
    while _search_timestamps and _search_timestamps[0] <= now - RATE_LIMIT_WINDOW:
        _search_timestamps.pop(0)
    if len(_search_timestamps) >= RATE_LIMIT_MAX:
        return False
    _search_timestamps.append(now)
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_url(url: str) -> str | None:
    """Return an error message if *url* is not a valid HTTP(S) URL, else None."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"Invalid URL scheme '{parsed.scheme}'. Only http and https are supported."
        if not parsed.netloc:
            return "Invalid URL: missing host."
    except Exception:
        return "Invalid URL: could not parse."
    return None


def _fetch_page_text(url: str, max_chars: int = MAX_FETCH_CHARS) -> str:
    """Fetch a URL and return the visible text, truncated to *max_chars*."""
    with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url, headers={"User-Agent": "MCP-WebSearch/1.0"})
        resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    return text[:max_chars]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def web_search(query: str, num_results: int = 5) -> dict:
    """Search the web using DuckDuckGo and return the top results.

    Use this tool when the user asks a question that requires up-to-date
    information from the internet, or when they explicitly ask to search.

    Args:
        query: The search query string.
        num_results: Number of results to return (1-10, default 5).

    Returns:
        Dictionary with the query, result count, and a list of results
        (each with title, url, and snippet).
    """
    num_results = max(1, min(num_results, 10))
    logger.info("Tool web_search invoked — query='%s', num=%d", query, num_results)

    if not query.strip():
        return {"query": query, "count": 0, "results": [], "error": "Empty query."}

    if not _check_rate_limit():
        logger.warning("Rate limit exceeded for web_search")
        return {
            "query": query,
            "count": 0,
            "results": [],
            "error": "Rate limit exceeded. Max 10 searches per minute.",
        }

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(query, max_results=num_results))
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
        ]
        if not results:
            return {
                "query": query,
                "count": 0,
                "results": [],
                "message": "No results found.",
            }
        return {"query": query, "count": len(results), "results": results}
    except Exception as exc:
        logger.error("web_search failed: %s", exc)
        return {"query": query, "count": 0, "results": [], "error": str(exc)}


@mcp.tool()
def fetch_url(url: str) -> dict:
    """Fetch a web page and return its visible text content (cleaned, no HTML).

    Use this tool when the user wants to read the content of a specific URL.
    Scripts, styles, and navigation elements are stripped.  Output is limited
    to 5 000 characters to keep responses concise.

    Args:
        url: The full URL of the page to fetch (http or https).

    Returns:
        Dictionary with the url, extracted text content, and character length.
    """
    logger.info("Tool fetch_url invoked — url='%s'", url)

    error = _validate_url(url)
    if error:
        return {"url": url, "content": "", "length": 0, "error": error}

    try:
        text = _fetch_page_text(url)
        return {"url": url, "content": text, "length": len(text)}
    except httpx.TimeoutException:
        logger.error("fetch_url timed out: %s", url)
        return {
            "url": url,
            "content": "",
            "length": 0,
            "error": "Request timed out (10s limit).",
        }
    except Exception as exc:
        logger.error("fetch_url failed: %s", exc)
        return {"url": url, "content": "", "length": 0, "error": str(exc)}


@mcp.tool()
def health_check() -> dict:
    """Check whether the Web Search server is healthy.

    Use this tool to verify the server is running and responsive.

    Returns:
        Dictionary with server status and timestamp.
    """
    logger.info("Tool health_check invoked")
    return {
        "status": "healthy",
        "server": "web-search",
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Entry-point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Web Search MCP server on port 8002 ...")
    mcp.run(transport="sse")
