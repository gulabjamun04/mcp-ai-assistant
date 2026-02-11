#!/usr/bin/env python3
"""Local integration test runner.

Starts all MCP servers and the agent, runs test queries, and reports results.
"""

import atexit
import signal
import socket
import subprocess
import sys
import time

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SERVERS = [
    {
        "name": "Note Manager",
        "module": "mcp_servers.note_manager.server",
        "port": 8001,
    },
    {
        "name": "Web Search",
        "module": "mcp_servers.web_search.server",
        "port": 8002,
    },
    {
        "name": "Doc Summarizer",
        "module": "mcp_servers.doc_summarizer.server",
        "port": 8003,
    },
]

AGENT = {
    "name": "FastAPI Agent",
    "module": "agent.main",
    "port": 8000,
}

AI_PARAGRAPH = (
    "Artificial intelligence has transformed nearly every sector of modern "
    "society, from healthcare and finance to education and entertainment. "
    "Machine learning algorithms now power recommendation systems, fraud "
    "detection pipelines, and autonomous vehicles. Deep learning, a subset "
    "of machine learning inspired by the structure of the human brain, has "
    "achieved remarkable breakthroughs in computer vision, natural language "
    "processing, and speech recognition. Large language models such as GPT "
    "and LLaMA have demonstrated an impressive ability to generate coherent "
    "text, answer complex questions, and even write functional code. "
    "However, these advances also raise important ethical questions about "
    "bias, transparency, and the displacement of human labour. Researchers "
    "are actively working on alignment techniques to ensure AI systems "
    "behave in ways that are safe and beneficial. The field continues to "
    "evolve at a breathtaking pace, with new architectures and training "
    "methods emerging every few months."
)

TESTS = [
    {
        "name": "Test 1 — Single tool: web_search",
        "message": "Search for recent news about large language models",
        "expected_tools": ["web_search"],
        "session_id": "test-1",
    },
    {
        "name": "Test 2 — Single tool: doc_summarizer",
        "message": f"Summarize this text: {AI_PARAGRAPH}",
        "expected_tools": ["summarize_text"],
        "session_id": "test-2",
    },
    {
        "name": "Test 3 — Single tool: note_manager",
        "message": (
            "Save a note titled 'Meeting Notes' with content "
            "'Discussed Q1 targets' and tag it with 'work'"
        ),
        "expected_tools": ["save_note"],
        "session_id": "test-3",
    },
    {
        "name": "Test 4 — Multi-tool chain",
        "message": (
            "Search for the latest news about MCP protocol, summarize "
            "what you find, and save it as a note titled 'MCP News'"
        ),
        "expected_tools": ["web_search", "summarize_text", "save_note"],
        "session_id": "test-4",
    },
]

CHAT_TIMEOUT = 360  # seconds per chat request (multi-tool chains need time)
STARTUP_TIMEOUT = 30  # seconds to wait for servers

# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------

_processes: list[subprocess.Popen] = []


def _cleanup() -> None:
    """Kill all child processes."""
    for proc in _processes:
        try:
            proc.terminate()
        except OSError:
            pass
    # Give them a moment, then force-kill
    time.sleep(1)
    for proc in _processes:
        try:
            proc.kill()
        except OSError:
            pass
    print("\n--- All processes cleaned up ---")


atexit.register(_cleanup)
signal.signal(signal.SIGINT, lambda *_: sys.exit(1))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))


def start_process(module: str, name: str) -> subprocess.Popen:
    """Start a Python module as a background process."""
    proc = subprocess.Popen(
        [sys.executable, "-m", module],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    _processes.append(proc)
    print(f"  Started {name} (PID {proc.pid})")
    return proc


def wait_for_port(port: int, name: str, timeout: int = STARTUP_TIMEOUT) -> bool:
    """Poll a port via TCP until it accepts connections or timeout is reached."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                print(f"  {name} (port {port}) is ready")
                return True
        except OSError:
            pass
        time.sleep(0.5)
    print(f"  TIMEOUT: {name} (port {port}) did not start in {timeout}s")
    return False


def wait_for_agent(port: int, timeout: int = STARTUP_TIMEOUT) -> bool:
    """Wait for the FastAPI agent to be ready."""
    url = f"http://localhost:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=5) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    print(f"  Agent (port {port}) is ready")
                    return True
        except (httpx.ConnectError, httpx.ReadTimeout):
            pass
        time.sleep(0.5)
    print(f"  TIMEOUT: Agent (port {port}) did not start in {timeout}s")
    return False


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


def run_test(test: dict, client: httpx.Client) -> bool:
    """Run a single test query and check results."""
    print(f"\n{'='*60}")
    print(f"{test['name']}")
    print(f"{'='*60}")
    print(f"Query: {test['message'][:100]}...")

    try:
        resp = client.post(
            "http://localhost:8000/chat",
            json={"message": test["message"], "session_id": test["session_id"]},
            timeout=CHAT_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"ERROR: Request failed — {e}")
        print("RESULT: FAIL")
        return False

    data = resp.json()
    tools_used = data.get("tools_used", [])
    response_text = data.get("response", "")
    latency = data.get("latency_ms", 0)

    print(f"Tools used: {tools_used}")
    print(f"Response: {response_text[:200]}...")
    print(f"Latency: {latency:.0f}ms")

    # Check that each expected tool appears in at least one used tool name
    # (tool names are namespaced like "note_manager__save_note")
    passed = True
    for expected in test["expected_tools"]:
        found = any(expected in tool for tool in tools_used)
        if not found:
            print(f"  MISSING expected tool: {expected}")
            passed = False

    print(f"RESULT: {'PASS' if passed else 'FAIL'}")
    return passed


def print_tools(client: httpx.Client) -> None:
    """Fetch and print available tools."""
    print(f"\n{'='*60}")
    print("Available MCP Tools")
    print(f"{'='*60}")
    try:
        resp = client.get("http://localhost:8000/tools", timeout=10)
        tools = resp.json()
        for t in tools:
            print(f"  [{t['server']}] {t['name']}")
        print(f"Total: {len(tools)} tools")
    except Exception as e:
        print(f"ERROR: Could not fetch tools — {e}")


def print_cache_stats(client: httpx.Client) -> None:
    """Fetch and print cache statistics."""
    print(f"\n{'='*60}")
    print("Cache Statistics")
    print(f"{'='*60}")
    try:
        resp = client.get("http://localhost:8000/cache/stats", timeout=10)
        stats = resp.json()
        print(f"  Hits:       {stats['hits']}")
        print(f"  Misses:     {stats['misses']}")
        print(f"  Hit rate:   {stats['hit_rate']:.2%}")
        print(f"  Cached keys: {stats['total_keys']}")
    except Exception as e:
        print(f"ERROR: Could not fetch cache stats — {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("MCP AI Assistant — Local Integration Tests")
    print("=" * 60)

    # 1. Start MCP servers
    print("\n--- Starting MCP servers ---")
    for srv in SERVERS:
        start_process(srv["module"], srv["name"])

    # 2. Wait for MCP servers to be healthy
    print("\n--- Waiting for MCP servers ---")
    for srv in SERVERS:
        if not wait_for_port(srv["port"], srv["name"]):
            print("FATAL: MCP server failed to start. Aborting.")
            return 1

    # 3. Start FastAPI agent
    print("\n--- Starting FastAPI Agent ---")
    start_process(AGENT["module"], AGENT["name"])

    # 4. Wait for agent
    print("\n--- Waiting for Agent ---")
    if not wait_for_agent(AGENT["port"]):
        print("FATAL: Agent failed to start. Aborting.")
        return 1

    # 5. Run tests (two rounds to verify cache behaviour)
    with httpx.Client() as client:
        print_tools(client)

        for run_number in (1, 2):
            print(f"\n{'#'*60}")
            print(
                f"  RUN {run_number} {'(expect cache misses)' if run_number == 1 else '(expect cache hits)'}"
            )
            print(f"{'#'*60}")

            results: list[bool] = []
            for test in TESTS:
                passed = run_test(test, client)
                results.append(passed)

            print_cache_stats(client)

            passed_count = sum(results)
            total = len(results)
            print(f"\n{'='*60}")
            print(f"RUN {run_number} SUMMARY: {passed_count}/{total} tests passed")
            print(f"{'='*60}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
