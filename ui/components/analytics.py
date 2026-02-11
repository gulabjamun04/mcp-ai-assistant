"""Analytics dashboard page: charts and recent activity table."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from ui import api

# Consistent color palette across all charts
_SERVER_COLORS: dict[str, str] = {
    "web_search": "#3498db",
    "note_manager": "#2ecc71",
    "doc_summarizer": "#e67e22",
    "calculator": "#9b59b6",
}

_CHART_LAYOUT: dict[str, Any] = {
    "paper_bgcolor": "rgba(0,0,0,0)",
    "plot_bgcolor": "rgba(0,0,0,0)",
    "font": {"size": 12},
    "margin": {"t": 30, "b": 50, "l": 60, "r": 20},
}

_SESSION_DEFAULTS: dict[str, Any] = {
    "total_sessions": 0,
    "avg_messages_per_session": 0.0,
    "active_last_hour": 0,
}


def _safe_fetch(fn: Any, default: Any) -> Any:
    """Call an API function, returning default on error."""
    try:
        return fn()
    except Exception:
        return default


def render() -> None:
    """Render the analytics dashboard."""
    st.title("📈 Analytics Dashboard")

    # Fetch all data upfront (avoids duplicate API calls)
    tool_data = _safe_fetch(api.get_tool_analytics, [])
    cache_data = _safe_fetch(api.get_cache_stats, {})
    session_data = _safe_fetch(api.get_session_analytics, _SESSION_DEFAULTS)
    recent_data = _safe_fetch(api.get_recent_invocations, [])

    _render_session_metrics(session_data)
    st.divider()

    col_left, col_right = st.columns(2)
    with col_left:
        _render_tool_usage_chart(tool_data)
    with col_right:
        _render_latency_chart(tool_data)

    st.divider()

    col_cache, col_recent = st.columns([1, 2])
    with col_cache:
        _render_cache_chart(cache_data)
    with col_recent:
        _render_recent_activity(recent_data)

    st.caption("Data refreshes on each page load.")


def _render_session_metrics(data: dict[str, Any]) -> None:
    """Show session-level KPIs."""
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Sessions", data.get("total_sessions", 0))
    col2.metric("Avg Messages / Session", data.get("avg_messages_per_session", 0))
    col3.metric("Active (last hour)", data.get("active_last_hour", 0))


def _render_tool_usage_chart(data: list[dict[str, Any]]) -> None:
    """Bar chart of invocation counts per tool."""
    st.subheader("Tool Usage")
    if not data:
        st.info("No tool invocations recorded yet.")
        return

    df = pd.DataFrame(data)
    df["short_name"] = df["tool_name"].str.replace("__", " / ", regex=False)

    fig = px.bar(
        df,
        x="short_name",
        y="total_calls",
        color="server_name",
        color_discrete_map=_SERVER_COLORS,
        labels={
            "short_name": "Tool",
            "total_calls": "Invocations",
            "server_name": "Server",
        },
        text="total_calls",
        height=350,
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        **_CHART_LAYOUT,
        showlegend=True,
        legend=dict(orientation="h", y=-0.25),
    )
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)")
    st.plotly_chart(fig, use_container_width=True)


def _render_latency_chart(data: list[dict[str, Any]]) -> None:
    """Bar chart of average latency per tool."""
    st.subheader("Average Latency")
    if not data:
        st.info("No data yet.")
        return

    df = pd.DataFrame(data)
    df["short_name"] = df["tool_name"].str.replace("__", " / ", regex=False)

    fig = px.bar(
        df,
        x="short_name",
        y="avg_latency_ms",
        color="server_name",
        color_discrete_map=_SERVER_COLORS,
        labels={
            "short_name": "Tool",
            "avg_latency_ms": "Avg Latency (ms)",
            "server_name": "Server",
        },
        text=df["avg_latency_ms"].apply(lambda v: f"{v:.0f} ms"),
        height=350,
    )
    fig.update_traces(textposition="outside")
    fig.update_layout(
        **_CHART_LAYOUT,
        showlegend=True,
        legend=dict(orientation="h", y=-0.25),
    )
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.15)")
    st.plotly_chart(fig, use_container_width=True)


def _render_cache_chart(stats: dict[str, Any]) -> None:
    """Donut chart of cache hits vs misses."""
    st.subheader("Cache Performance")
    hits = stats.get("hits", 0)
    misses = stats.get("misses", 0)

    if hits == 0 and misses == 0:
        st.info("No cache activity yet.")
        return

    fig = px.pie(
        names=["Hits", "Misses"],
        values=[hits, misses],
        color=["Hits", "Misses"],
        color_discrete_map={"Hits": "#2ecc71", "Misses": "#e74c3c"},
        hole=0.4,
        height=300,
    )
    fig.update_traces(textinfo="label+percent", textfont_size=13)
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=20, b=20, l=20, r=20),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_recent_activity(data: list[dict[str, Any]]) -> None:
    """Table of last 20 tool invocations."""
    st.subheader("Recent Activity")
    if not data:
        st.info("No recent invocations.")
        return

    df = pd.DataFrame(data)

    display_cols = {
        "tool_name": "Tool",
        "server_name": "Server",
        "latency_ms": "Latency (ms)",
        "cache_hit": "Cache Hit",
        "status": "Status",
        "created_at": "Time",
    }
    available = [c for c in display_cols if c in df.columns]
    df_display = df[available].rename(columns=display_cols)

    st.dataframe(df_display, use_container_width=True, hide_index=True)
