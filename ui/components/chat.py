"""Chat page: conversation interface with tool badges and latency display."""

from __future__ import annotations

import time
import uuid

import requests
import streamlit as st

from ui import api

# Map server prefixes to colored badge labels
_TOOL_BADGES: dict[str, str] = {
    "web_search": "🔍 web_search",
    "note_manager": "📝 note_manager",
    "doc_summarizer": "📄 summarizer",
    "calculator": "🧮 calculator",
}

# Welcome screen example prompts — (button label, actual prompt text)
_EXAMPLES: list[tuple[str, str]] = [
    ("🔍 Search for latest AI news", "Search for the latest AI news"),
    ("📝 Save a note about my project ideas", "Save a note about my project ideas"),
    ("📄 Summarize a topic for me", "Summarize a topic for me"),
    (
        "🔗 Search and summarize news about MCP protocol",
        "Search and summarize news about MCP protocol",
    ),
]

# Demo mode queries
DEMO_QUERIES: list[str] = [
    "Search for the latest news about AI agents",
    "Save a note titled 'AI Research' with content about recent developments in AI agents and tool use",
    "Summarize the concept of Model Context Protocol for me",
    "What notes do I have saved?",
]

_DEMO_DELAY = 3  # seconds between demo queries


def _tool_badge(tool_name: str) -> str:
    """Return a badge string for a tool name."""
    for prefix, badge in _TOOL_BADGES.items():
        if tool_name.startswith(prefix):
            return badge
    return f"⚙️ {tool_name}"


def _ensure_session() -> None:
    """Initialize session state on first load."""
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "messages" not in st.session_state:
        st.session_state.messages = []


def _render_welcome() -> None:
    """Show welcome message and example prompts when chat is empty."""
    st.markdown(
        "👋 **Hi! I'm your MCP-powered AI assistant.** "
        "I can search the web, summarize documents, manage your notes, "
        "and more. Try asking me something!"
    )
    st.write("")
    cols = st.columns(2)
    for i, (label, prompt_text) in enumerate(_EXAMPLES):
        with cols[i % 2]:
            if st.button(label, use_container_width=True, key=f"example_{i}"):
                st.session_state.pending_prompt = prompt_text
                st.rerun()


def _send_message(prompt: str) -> bool:
    """Send a message to the backend and render the response.

    Returns True on success, False on error.
    """
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Thinking... this may take a minute for summarization tasks"):
            try:
                result = api.chat(prompt, st.session_state.session_id)
                response = result["response"]
                tools_used = result.get("tools_used", [])
                latency_ms = result.get("latency_ms", 0)
            except requests.ConnectionError:
                st.error(
                    "Cannot reach the backend API. "
                    "Make sure the FastAPI server is running on port 8000."
                )
                st.session_state.messages.pop()
                return False
            except requests.Timeout:
                st.error("Request timed out. The server may be overloaded.")
                st.session_state.messages.pop()
                return False
            except Exception as e:
                st.error(f"Request failed: {e}")
                st.session_state.messages.pop()
                return False

        st.markdown(response)

        msg_data = {
            "role": "assistant",
            "content": response,
            "tools_used": tools_used,
            "latency_ms": latency_ms,
        }
        _render_metadata(msg_data)
        st.session_state.messages.append(msg_data)
    return True


def render() -> None:
    """Render the chat page."""
    _ensure_session()

    st.title("💬 Chat")

    # Display conversation history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant":
                _render_metadata(msg)

    # Welcome screen (only when no messages and no pending input)
    if (
        not st.session_state.messages
        and "pending_prompt" not in st.session_state
        and not st.session_state.get("demo_active")
    ):
        _render_welcome()

    # Demo mode processing
    if st.session_state.get("demo_active"):
        demo_idx = st.session_state.get("demo_index", 0)
        if demo_idx < len(DEMO_QUERIES):
            ok = _send_message(DEMO_QUERIES[demo_idx])
            if ok:
                st.session_state.demo_index = demo_idx + 1
                if st.session_state.demo_index < len(DEMO_QUERIES):
                    time.sleep(_DEMO_DELAY)
                    st.rerun()
                else:
                    # Demo complete — signal sidebar to reset the toggle
                    st.session_state.demo_active = False
                    st.session_state._demo_finished = True
                    st.toast("Demo complete!")
                    st.rerun()
            else:
                # Backend error — stop demo, fall through to chat input
                st.session_state.demo_active = False
                st.session_state._demo_finished = True

    # Normal chat input (hidden during active demo runs)
    if not st.session_state.get("demo_active"):
        prompt = st.chat_input("Ask me anything...")
        if "pending_prompt" in st.session_state:
            prompt = st.session_state.pop("pending_prompt")

        if prompt:
            _send_message(prompt)


def _render_metadata(msg: dict) -> None:
    """Show tool badges and latency below an assistant message."""
    tools = msg.get("tools_used", [])
    latency = msg.get("latency_ms", 0)

    if not tools and not latency:
        return

    parts: list[str] = []
    if tools:
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for t in tools:
            badge = _tool_badge(t)
            if badge not in seen:
                seen.add(badge)
                unique.append(badge)
        parts.append("  ".join(f"`{b}`" for b in unique))
    if latency:
        parts.append(f"*{latency:.0f} ms*")

    st.caption(" · ".join(parts))
