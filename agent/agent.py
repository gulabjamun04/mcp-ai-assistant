"""LangChain Agent with MCP tool integration.

Uses ChatOllama with tool calling, conversation memory,
and multi-step tool chain support via LangGraph's ReAct agent.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langchain_ollama import ChatOllama
from langgraph.prebuilt import create_react_agent

from agent.config import Settings
from agent.mcp_client import MCPToolRegistry, _current_session_id
from agent.metrics import ACTIVE_SESSIONS

logger = logging.getLogger(__name__)

MAX_HISTORY = 10  # message pairs per session (user + assistant = 2 entries)

# Regex to strip Qwen3 thinking blocks: <think>...</think>
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass
class AgentResponse:
    """Result of an agent chat invocation."""

    response: str
    tools_used: list[str]
    latency_ms: float


class AIAgent:
    """Core AI agent that orchestrates LLM reasoning and MCP tool calls."""

    def __init__(self, settings: Settings, registry: MCPToolRegistry) -> None:
        self.settings = settings
        self.registry = registry
        self.db: Any = None  # Database instance, set during startup
        self._sessions: dict[str, list] = {}

    async def chat(self, message: str, session_id: str) -> AgentResponse:
        """Process a user message and return the agent's response."""
        start = time.perf_counter()
        token = _current_session_id.set(session_id)

        try:
            history = self._sessions.get(session_id, [])

            model = ChatOllama(
                model=self.settings.ollama_model,
                base_url=self.settings.ollama_base_url,
            )

            tools = self.registry.langchain_tools
            agent = create_react_agent(model, tools)

            input_messages = list(history) + [HumanMessage(content=message)]

            try:
                result = await agent.ainvoke({"messages": input_messages})
            except Exception as e:
                logger.error("Agent invocation failed: %s", e)
                latency_ms = (time.perf_counter() - start) * 1000
                return AgentResponse(
                    response=f"Sorry, I encountered an error: {e}",
                    tools_used=[],
                    latency_ms=round(latency_ms, 1),
                )

            # Extract tools used and final response
            output_messages = result["messages"]
            tools_used: list[str] = []
            response_text = ""

            for msg in output_messages:
                if isinstance(msg, AIMessage) and msg.tool_calls:
                    for tc in msg.tool_calls:
                        tools_used.append(tc["name"])

            # Find the last AI message with content (the final answer)
            for msg in reversed(output_messages):
                if isinstance(msg, AIMessage) and msg.content:
                    response_text = msg.content
                    break

            if not response_text:
                response_text = "I couldn't generate a response."

            # Strip Qwen3 <think>...</think> blocks
            response_text = _THINK_RE.sub("", response_text).strip()

            # Update session history (keep last N entries)
            is_new_session = session_id not in self._sessions
            history.append(HumanMessage(content=message))
            history.append(AIMessage(content=response_text))
            self._sessions[session_id] = history[-(MAX_HISTORY * 2) :]
            if is_new_session:
                ACTIVE_SESSIONS.set(len(self._sessions))

            latency_ms = (time.perf_counter() - start) * 1000

            logger.info(
                "Chat session=%s tools=%s latency=%.0fms",
                session_id,
                tools_used,
                latency_ms,
            )

            # Log conversation to PostgreSQL (fire-and-forget)
            if self.db:
                asyncio.create_task(
                    self.db.log_conversation(session_id, "user", message)
                )
                asyncio.create_task(
                    self.db.log_conversation(
                        session_id, "assistant", response_text, tools_used
                    )
                )

            return AgentResponse(
                response=response_text,
                tools_used=tools_used,
                latency_ms=round(latency_ms, 1),
            )
        finally:
            _current_session_id.reset(token)
