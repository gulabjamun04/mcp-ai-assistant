#!/usr/bin/env python3
"""Docker integration test runner.

Assumes `docker compose up` is already running.
Polls health endpoints, runs chat queries, and validates the full stack.
"""

import socket
import sys
import time

import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AGENT_URL = "http://localhost:8000"
CHAT_TIMEOUT = 360  # seconds per chat request
HEALTH_TIMEOUT = 60  # seconds to wait for all services

HTTP_HEALTH = [
    ("Agent", f"{AGENT_URL}/health"),
    ("Streamlit UI", "http://localhost:8501/_stcore/health"),
]

TCP_HEALTH = [
    ("Note Manager", "localhost", 8001),
    ("Web Search", "localhost", 8002),
    ("Doc Summarizer", "localhost", 8003),
]

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
        "label": "web_search",
        "message": "Search for recent news about large language models",
        "expected_tools": ["web_search"],
        "session_id": "docker-test-1",
    },
    {
        "label": "summarizer",
        "message": f"Summarize this text: {AI_PARAGRAPH}",
        "expected_tools": ["summarize_text"],
        "session_id": "docker-test-2",
    },
    {
        "label": "note_manager",
        "message": (
            "Save a note titled 'Docker Test' with content "
            "'Testing the Docker deployment' and tag it with 'test'"
        ),
        "expected_tools": ["save_note"],
        "session_id": "docker-test-3",
    },
    {
        "label": "multi-tool chain",
        "message": (
            "Search for the latest news about MCP protocol, summarize "
            "what you find, and save it as a note titled 'MCP News'"
        ),
        "expected_tools": ["web_search", "summarize_text", "save_note"],
        "session_id": "docker-test-4",
    },
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class Results:
    """Collects pass/fail lines for the final summary."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.all_passed = True

    def ok(self, msg: str) -> None:
        self.lines.append(f"  \u2705 {msg}")

    def fail(self, msg: str) -> None:
        self.lines.append(f"  \u274c {msg}")
        self.all_passed = False

    def print_summary(self) -> None:
        print("\n" + "=" * 60)
        print("  DOCKER INTEGRATION TEST SUMMARY")
        print("=" * 60)
        for line in self.lines:
            print(line)
        print()
        if self.all_passed:
            print("  \U0001f389 All tests passed! System is demo-ready.")
        else:
            print("  \u26a0\ufe0f  Some tests failed. Check output above.")
        print("=" * 60)


def poll_http(url: str, timeout: int) -> bool:
    """Poll a URL until it returns 2xx or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=5) as c:
                resp = c.get(url)
                if resp.status_code < 400:
                    return True
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError):
            pass
        time.sleep(2)
    return False


def poll_tcp(host: str, port: int, timeout: int) -> bool:
    """Poll a TCP port until it accepts connections or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            pass
        time.sleep(2)
    return False


# ---------------------------------------------------------------------------
# Test steps
# ---------------------------------------------------------------------------


def check_services(results: Results) -> bool:
    """Step 1: Wait for all services to be healthy."""
    print("\n--- Step 1: Checking service health (max 60s) ---")
    all_healthy = True
    for name, url in HTTP_HEALTH:
        sys.stdout.write(f"  Waiting for {name}... ")
        sys.stdout.flush()
        if poll_http(url, HEALTH_TIMEOUT):
            print("OK")
        else:
            print("TIMEOUT")
            all_healthy = False
    for name, host, port in TCP_HEALTH:
        sys.stdout.write(f"  Waiting for {name} (port {port})... ")
        sys.stdout.flush()
        if poll_tcp(host, port, HEALTH_TIMEOUT):
            print("OK")
        else:
            print("TIMEOUT")
            all_healthy = False
    if all_healthy:
        results.ok("All services healthy")
    else:
        results.fail("Some services unreachable")
    return all_healthy


def run_chat_tests(client: httpx.Client, results: Results) -> None:
    """Step 2: Run the 4 chat test queries."""
    print("\n--- Step 2: Running chat tests ---")
    for i, test in enumerate(TESTS, 1):
        sys.stdout.write(f"  Test {i}: {test['label']}... ")
        sys.stdout.flush()
        start = time.time()
        try:
            resp = client.post(
                f"{AGENT_URL}/chat",
                json={"message": test["message"], "session_id": test["session_id"]},
                timeout=CHAT_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            elapsed = time.time() - start
            tools_used = data.get("tools_used", [])

            # Check expected tools appear (namespaced names)
            missing = []
            for expected in test["expected_tools"]:
                if not any(expected in t for t in tools_used):
                    missing.append(expected)

            if missing:
                print(f"FAIL ({elapsed:.1f}s) — missing tools: {missing}")
                results.fail(
                    f"Test {i}: {test['label']} \u2014 FAIL ({elapsed:.1f}s) "
                    f"missing: {missing}"
                )
            else:
                print(f"PASS ({elapsed:.1f}s)")
                results.ok(f"Test {i}: {test['label']} \u2014 PASS ({elapsed:.1f}s)")
        except Exception as e:
            elapsed = time.time() - start
            print(f"ERROR ({elapsed:.1f}s) — {e}")
            results.fail(f"Test {i}: {test['label']} \u2014 ERROR ({elapsed:.1f}s)")


def check_tools(client: httpx.Client, results: Results) -> None:
    """Step 3: Verify GET /tools returns expected tools."""
    print("\n--- Step 3: Checking tool discovery ---")
    try:
        resp = client.get(f"{AGENT_URL}/tools", timeout=10)
        resp.raise_for_status()
        tools = resp.json()
        count = len(tools)
        print(f"  Discovered {count} tools")
        for t in tools:
            print(f"    [{t['server']}] {t['name']}")
        if count >= 7:  # 4 note_manager + 3 web_search + 3 doc_summarizer = 10
            results.ok(f"Tools discovered: {count}")
        else:
            results.fail(f"Tools discovered: {count} (expected >= 7)")
    except Exception as e:
        print(f"  ERROR: {e}")
        results.fail(f"Tool discovery failed: {e}")


def check_cache(client: httpx.Client, results: Results) -> None:
    """Step 4: Verify GET /cache/stats works."""
    print("\n--- Step 4: Checking cache ---")
    try:
        resp = client.get(f"{AGENT_URL}/cache/stats", timeout=10)
        resp.raise_for_status()
        stats = resp.json()
        print(
            f"  Hits: {stats.get('hits', '?')}, Misses: {stats.get('misses', '?')}, "
            f"Keys: {stats.get('total_keys', '?')}"
        )
        results.ok("Cache working")
    except Exception as e:
        print(f"  ERROR: {e}")
        results.fail(f"Cache check failed: {e}")


def check_analytics(client: httpx.Client, results: Results) -> None:
    """Step 5: Verify GET /analytics/tools works."""
    print("\n--- Step 5: Checking analytics ---")
    try:
        resp = client.get(f"{AGENT_URL}/analytics/tools", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        print(f"  Tool analytics entries: {len(data)}")
        for entry in data[:5]:
            print(
                f"    {entry.get('tool_name', '?')}: "
                f"{entry.get('total_calls', '?')} calls, "
                f"avg {entry.get('avg_latency_ms', 0):.0f}ms"
            )
        results.ok("Analytics working")
    except Exception as e:
        print(f"  ERROR: {e}")
        results.fail(f"Analytics check failed: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    print("=" * 60)
    print("  MCP AI Assistant \u2014 Docker Integration Tests")
    print("=" * 60)

    results = Results()

    # Step 1: Health checks
    if not check_services(results):
        print("\nServices not ready. Is `docker compose up` running?")
        results.print_summary()
        return 1

    # Steps 2-5: Run against the live stack
    with httpx.Client() as client:
        run_chat_tests(client, results)
        check_tools(client, results)
        check_cache(client, results)
        check_analytics(client, results)

    results.print_summary()
    return 0 if results.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
