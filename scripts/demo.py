#!/usr/bin/env python3
"""
MCP Dynamic Tool Discovery Demo

Demonstrates live hot-plugging of MCP tool servers — a new calculator
server is started while the agent is running, tools are discovered on
the fly, and the agent immediately routes queries to the new tools.
Zero code changes.  Zero restarts.
"""

import subprocess
import sys
import time

import requests

AGENT_URL = "http://localhost:8000"
TIMEOUT = 300  # seconds per chat request (LLM can be slow with many tools)

# ---------------------------------------------------------------------------
# ANSI colours
# ---------------------------------------------------------------------------
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
WHITE = "\033[97m"


def banner(text: str) -> None:
    """Print a bold cyan banner."""
    width = 60
    print()
    print(f"{CYAN}{BOLD}{'=' * width}{RESET}")
    print(f"{CYAN}{BOLD}  {text}{RESET}")
    print(f"{CYAN}{BOLD}{'=' * width}{RESET}")
    print()


def step(number: int, title: str) -> None:
    """Print a step header."""
    print(f"\n{YELLOW}{BOLD}--- Step {number}: {title} ---{RESET}\n")


def info(msg: str) -> None:
    """Print an info line."""
    print(f"  {DIM}{msg}{RESET}")


def success(msg: str) -> None:
    """Print a success line."""
    print(f"  {GREEN}{msg}{RESET}")


def highlight(msg: str) -> None:
    """Print a highlighted line."""
    print(f"  {MAGENTA}{BOLD}{msg}{RESET}")


def agent_chat(message: str, session_id: str = "demo") -> dict:
    """Send a chat message to the agent and return the response."""
    resp = requests.post(
        f"{AGENT_URL}/chat",
        json={"message": message, "session_id": session_id},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def get_tools() -> list[dict]:
    """Fetch the list of available tools."""
    resp = requests.get(f"{AGENT_URL}/tools", timeout=10)
    resp.raise_for_status()
    return resp.json()


def refresh_tools() -> dict:
    """Trigger tool re-discovery."""
    resp = requests.post(f"{AGENT_URL}/tools/refresh", timeout=30)
    resp.raise_for_status()
    return resp.json()


def pause(seconds: float = 2.0) -> None:
    """Dramatic pause for screen-recording effect."""
    time.sleep(seconds)


def print_tools_summary(tools: list[dict]) -> None:
    """Print a grouped summary of tools by server."""
    servers: dict[str, list[str]] = {}
    for t in tools:
        servers.setdefault(t["server"], []).append(t["name"].split("__", 1)[-1])
    for server, names in sorted(servers.items()):
        print(f"    {BLUE}{server}{RESET}: {', '.join(sorted(names))}")


def print_chat_result(result: dict) -> None:
    """Print a formatted chat response."""
    tools = result.get("tools_used", [])
    latency = result.get("latency_ms", 0)
    response = result.get("response", "")

    if tools:
        tool_badges = " ".join(
            f"{MAGENTA}[{t.split('__', 1)[-1]}]{RESET}" for t in tools
        )
        print(f"  {DIM}Tools used:{RESET} {tool_badges}")
    else:
        print(f"  {DIM}Tools used:{RESET} {DIM}(none — LLM only){RESET}")

    print(f"  {DIM}Latency:{RESET} {latency:.0f}ms")
    # Truncate long responses
    lines = response.strip().splitlines()
    preview = "\n    ".join(lines[:6])
    if len(lines) > 6:
        preview += f"\n    {DIM}...({len(lines) - 6} more lines){RESET}"
    print(f"  {WHITE}Response:{RESET}")
    print(f"    {preview}")


# ---------------------------------------------------------------------------
# Demo flow
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the full dynamic discovery demo."""
    banner("\U0001f3ac MCP Dynamic Tool Discovery Demo")

    # Verify agent is reachable
    try:
        requests.get(f"{AGENT_URL}/health", timeout=5)
    except requests.ConnectionError:
        print(f"{RED}ERROR: Agent not reachable at {AGENT_URL}{RESET}")
        print(f"{RED}Run 'docker compose up -d' first.{RESET}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Step 1 — Show current tools (WITHOUT calculator)
    # ------------------------------------------------------------------
    step(1, "Current tools (before calculator)")

    # Make sure calculator is NOT running
    info("Ensuring calculator server is stopped...")
    subprocess.run(
        ["docker", "compose", "stop", "mcp-calculator"],
        capture_output=True,
    )
    pause(2)

    # Refresh to drop any stale calculator tools
    refresh_tools()
    pause(1)

    tools_before = get_tools()
    servers_before = set(t["server"] for t in tools_before)

    success(
        f"Available: {len(tools_before)} tools from " f"{len(servers_before)} servers"
    )
    print_tools_summary(tools_before)
    pause(2)

    # ------------------------------------------------------------------
    # Step 2 — Ask a math question WITHOUT calculator
    # ------------------------------------------------------------------
    step(2, "Math query WITHOUT calculator")
    query = "What is 15% of 250?"
    highlight(f'Asking: "{query}"')
    pause(1)

    result_before = agent_chat(query, session_id="demo-before")
    print_chat_result(result_before)
    pause(2)

    # ------------------------------------------------------------------
    # Step 3 — Start the calculator server
    # ------------------------------------------------------------------
    step(3, "Starting calculator MCP server")
    print(f"  {CYAN}\U0001f680 Launching mcp-calculator container...{RESET}")

    subprocess.run(
        ["docker", "compose", "up", "-d", "--build", "mcp-calculator"],
        capture_output=True,
    )

    # Wait for healthy
    info("Waiting for calculator to become healthy...")
    for i in range(30):
        time.sleep(2)
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                "mcp-calculator",
            ],
            capture_output=True,
            text=True,
        )
        status = result.stdout.strip()
        if status == "healthy":
            success(f"\u2705 Calculator server is healthy! (took ~{(i+1)*2}s)")
            break
        info(f"  Status: {status} ({(i+1)*2}s elapsed)...")
    else:
        print(f"  {RED}Calculator did not become healthy in 60s{RESET}")
        sys.exit(1)

    pause(2)

    # ------------------------------------------------------------------
    # Step 4 — Trigger tool refresh and show new tools
    # ------------------------------------------------------------------
    step(4, "Discovering new tools")
    info("Triggering POST /tools/refresh ...")

    changes = refresh_tools()
    tools_after = get_tools()
    servers_after = set(t["server"] for t in tools_after)

    added = changes.get("changes", {}).get("added", [])
    if added:
        added_names = ", ".join(n.split("__", 1)[-1] for n in added)
        success(f"\u2705 New tools discovered: {added_names}")
    else:
        success("\u2705 Tool refresh complete")

    success(
        f"Available: {len(tools_after)} tools from " f"{len(servers_after)} servers"
    )
    print_tools_summary(tools_after)
    pause(2)

    # ------------------------------------------------------------------
    # Step 5 — Same math question WITH calculator
    # ------------------------------------------------------------------
    step(5, "Math query WITH calculator")
    highlight(f'Same question: "{query}"')
    pause(1)

    result_after = agent_chat(query, session_id="demo-after")
    print_chat_result(result_after)
    pause(2)

    # ------------------------------------------------------------------
    # Step 6 — Unit conversion (new capability)
    # ------------------------------------------------------------------
    step(6, "Unit conversion (brand-new capability)")
    conversion_query = "Convert 100 kilometers to miles"
    highlight(f'Asking: "{conversion_query}"')
    pause(1)

    result_convert = agent_chat(conversion_query, session_id="demo-convert")
    print_chat_result(result_convert)
    pause(2)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    tools_used_before = result_before.get("tools_used", [])
    tools_used_after = result_after.get("tools_used", [])
    tools_used_convert = result_convert.get("tools_used", [])

    calc_before = any("calculator" in t for t in tools_used_before)
    calc_after = any("calculator" in t for t in tools_used_after)
    convert_used = any("convert" in t for t in tools_used_convert)

    banner("\U0001f3af Demo Complete!")

    print(
        f"  {BLUE}Before:{RESET} {len(servers_before)} servers, "
        f"{len(tools_before)} tools"
    )
    if not calc_before:
        print(f"         Math query answered by {DIM}LLM reasoning alone{RESET}")
    else:
        print(f"         Math query used calculator tool")

    print()
    print(
        f"  {GREEN}After:{RESET}  {len(servers_after)} servers, "
        f"{len(tools_after)} tools"
    )
    if calc_after:
        print(
            f"         Math query routed to "
            f"{MAGENTA}dedicated calculator tool{RESET}"
        )
    else:
        print(
            f"         Math query answered by LLM "
            f"(calculator available but not used)"
        )
    if convert_used:
        print(
            f"         Unit conversion handled by "
            f"{MAGENTA}convert_units tool{RESET}"
        )

    print()
    print(
        f"  {CYAN}{BOLD}Zero code changes. Zero restarts. "
        f"Pure MCP magic. \u2728{RESET}"
    )
    print()


if __name__ == "__main__":
    main()
