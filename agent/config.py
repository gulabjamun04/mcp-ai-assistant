"""Agent configuration loaded from environment variables."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_settings import BaseSettings


@dataclass
class MCPServerConfig:
    """Configuration for a single MCP server."""

    name: str
    url: str


class Settings(BaseSettings):
    """Application settings loaded from .env file."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:1.7b"

    # MCP Servers
    mcp_note_manager_url: str = "http://localhost:8001"
    mcp_web_search_url: str = "http://localhost:8002"
    mcp_doc_summarizer_url: str = "http://localhost:8003"
    mcp_calculator_url: str = "http://localhost:8004"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # PostgreSQL
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/mcp_assistant"
    )

    @property
    def mcp_servers(self) -> list[MCPServerConfig]:
        """Return list of configured MCP servers."""
        return [
            MCPServerConfig("note_manager", self.mcp_note_manager_url),
            MCPServerConfig("web_search", self.mcp_web_search_url),
            MCPServerConfig("doc_summarizer", self.mcp_doc_summarizer_url),
            MCPServerConfig("calculator", self.mcp_calculator_url),
        ]


settings = Settings()
