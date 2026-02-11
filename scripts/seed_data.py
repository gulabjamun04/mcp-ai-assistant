"""Seed the system with realistic data for screenshots.

Sends 10 diverse queries across multiple sessions to populate
PostgreSQL (conversations + tool invocations) and Redis (cache).
Requires all services to be running (docker compose up).

Usage:
    python scripts/seed_data.py [--base-url http://localhost:8000]
"""

from __future__ import annotations

import argparse
import sys
import time

import requests

DEFAULT_BASE_URL = "http://localhost:8000"
TIMEOUT = 180  # LLM calls can be slow on first run


# Each entry: (session_id, message, description)
QUERIES: list[tuple[str, str, str]] = [
    # --- Web Search (3) ---
    (
        "session-web-1",
        "Search the web for the latest trends in large language models in 2025.",
        "Web search — LLM trends",
    ),
    (
        "session-web-1",
        "Search the web for how Model Context Protocol works and its benefits.",
        "Web search — MCP overview",
    ),
    (
        "session-web-2",
        "Search for popular open-source AI agent frameworks.",
        "Web search — AI agent frameworks",
    ),
    # --- Note Saving (3) ---
    (
        "session-notes-1",
        "Save a note titled 'Project Ideas' with content: 'Build an MCP-powered "
        "code review assistant that connects to GitHub and runs static analysis "
        "tools as MCP servers.' Tag it with 'ideas' and 'mcp'.",
        "Save note — project ideas",
    ),
    (
        "session-notes-1",
        "Save a note titled 'Meeting Notes' with content: 'Discussed migrating "
        "the monolith to microservices. Key decision: use event-driven "
        "architecture with Redis Streams for inter-service communication.' "
        "Tag it with 'meetings' and 'architecture'.",
        "Save note — meeting notes",
    ),
    (
        "session-notes-2",
        "Save a note titled 'Reading List' with content: 'Papers to read: "
        "Attention Is All You Need, ReAct: Synergizing Reasoning and Acting, "
        "Toolformer: Language Models Can Teach Themselves to Use Tools.' "
        "Tag it with 'reading' and 'papers'.",
        "Save note — reading list",
    ),
    # --- Summarization (2) ---
    (
        "session-summarize-1",
        "Summarize this text: 'The Model Context Protocol (MCP) is an open "
        "standard that enables seamless integration between AI applications and "
        "external data sources and tools. It provides a standardized way for AI "
        "models to access context from various systems, replacing fragmented "
        "integrations with a single protocol. MCP follows a client-server "
        "architecture where host applications connect to MCP servers that expose "
        "specific capabilities like database access, API integrations, or file "
        "system operations. This modular approach means developers can build "
        "reusable tool servers that any MCP-compatible AI application can use.'",
        "Summarize — MCP description",
    ),
    (
        "session-summarize-1",
        "Extract the key points from this text: 'Docker Compose is a tool for "
        "defining and running multi-container applications. With Compose, you use "
        "a YAML file to configure your application services. Then, with a single "
        "command, you create and start all the services. Compose works in all "
        "environments: production, staging, development, testing, as well as CI "
        "workflows. It also has commands for managing the whole lifecycle of your "
        "application: start, stop, and rebuild services; view the status of "
        "running services; stream the log output; and run one-off commands.'",
        "Key points — Docker Compose",
    ),
    # --- Multi-tool chains (2) ---
    (
        "session-chain-1",
        "Search the web for 'benefits of ReAct agents', then save the results "
        "as a note titled 'ReAct Agent Benefits' tagged with 'research' and 'agents'.",
        "Multi-tool — search + save note",
    ),
    (
        "session-chain-2",
        "What is 15% of 2500, and also convert 42 kilometers to miles?",
        "Multi-tool — calculate + convert",
    ),
]


def check_health(base_url: str) -> bool:
    """Verify the agent is reachable and healthy."""
    try:
        resp = requests.get(f"{base_url}/health", timeout=10)
        data = resp.json()
        return data.get("agent") == "healthy"
    except Exception as e:
        print(f"  Health check failed: {e}")
        return False


def send_query(
    base_url: str, session_id: str, message: str
) -> dict:
    """Send a single chat query and return the response."""
    resp = requests.post(
        f"{base_url}/chat",
        json={"message": message, "session_id": session_id},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    """Run all seed queries sequentially."""
    parser = argparse.ArgumentParser(description="Seed data for screenshots")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Agent API base URL (default: {DEFAULT_BASE_URL})",
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    print(f"\n  Seeding data via {base_url}")
    print("  =" * 30)

    # Health check
    print("\n  [0/10] Checking agent health...")
    if not check_health(base_url):
        print("  FAIL: Agent is not healthy. Is docker compose up?")
        sys.exit(1)
    print("  OK: Agent is healthy.\n")

    total_time = 0.0
    tools_seen: set[str] = set()

    for i, (session_id, message, description) in enumerate(QUERIES, 1):
        print(f"  [{i}/10] {description}")
        print(f"         Session: {session_id}")
        print(f"         Query:   {message[:80]}{'...' if len(message) > 80 else ''}")

        start = time.time()
        try:
            result = send_query(base_url, session_id, message)
            elapsed = time.time() - start
            total_time += elapsed

            tools_used = result.get("tools_used", [])
            tools_seen.update(tools_used)
            response_preview = result["response"][:120].replace("\n", " ")

            print(f"         Tools:   {tools_used or '(none)'}")
            print(f"         Time:    {elapsed:.1f}s")
            print(f"         Reply:   {response_preview}...")
            print()
        except Exception as e:
            elapsed = time.time() - start
            total_time += elapsed
            print(f"         ERROR:   {e} ({elapsed:.1f}s)")
            print()

    # Summary
    print("  " + "=" * 58)
    print(f"  Done! 10 queries sent across {len({q[0] for q in QUERIES})} sessions.")
    print(f"  Total time: {total_time:.1f}s")
    print(f"  Tools invoked: {sorted(tools_seen) or '(none)'}")
    print()
    print("  Data is now in PostgreSQL and Redis. Ready for screenshots:")
    print("    - Streamlit Chat:      http://localhost:8501")
    print("    - Streamlit Analytics: http://localhost:8501 (Analytics page)")
    print("    - Grafana Dashboard:   http://localhost:3000")
    print("    - Agent API Docs:      http://localhost:8000/docs")
    print()


if __name__ == "__main__":
    main()
