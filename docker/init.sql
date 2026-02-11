-- PostgreSQL initialization script for MCP AI Assistant
-- Runs automatically when the container starts for the first time.

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    message_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tool_invocations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    tool_name VARCHAR(255) NOT NULL,
    server_name VARCHAR(255) NOT NULL,
    input_data JSONB,
    output_data JSONB,
    latency_ms DOUBLE PRECISION NOT NULL,
    cache_hit BOOLEAN NOT NULL DEFAULT FALSE,
    status VARCHAR(50) NOT NULL DEFAULT 'success',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    role VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    tools_used JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tool_invocations_session
    ON tool_invocations(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_invocations_created
    ON tool_invocations(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_session
    ON conversations(session_id);
