"""MCP AI Assistant — Streamlit chat interface.

Run with:
    streamlit run ui/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is on sys.path so `ui.*` imports resolve
# regardless of the working directory Streamlit uses.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import streamlit as st  # noqa: E402

st.set_page_config(
    page_title="MCP AI Assistant",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ui import api  # noqa: E402
from ui.components import analytics, chat, sidebar  # noqa: E402

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

page = st.navigation(
    [
        st.Page(chat.render, title="Chat", icon="💬", default=True, url_path="chat"),
        st.Page(analytics.render, title="Analytics", icon="📈", url_path="analytics"),
    ]
)

# Sidebar is shared across all pages
sidebar.render()

# Render the selected page
page.run()

# Footer
st.divider()
try:
    _health = api.get_health()
    _model_name = _health.get("model", "Ollama")
except Exception:
    _model_name = "Ollama"
st.caption(f"Built with MCP Protocol | Powered by {_model_name}")
