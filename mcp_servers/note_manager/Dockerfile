FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir mcp pydantic

# Copy package structure for relative imports
COPY mcp_servers/__init__.py ./mcp_servers/__init__.py
COPY mcp_servers/note_manager/ ./mcp_servers/note_manager/

EXPOSE 8001

CMD ["python", "-m", "mcp_servers.note_manager.server"]
