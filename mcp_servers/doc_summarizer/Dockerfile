FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir mcp httpx

# Copy package structure for relative imports
COPY mcp_servers/__init__.py ./mcp_servers/__init__.py
COPY mcp_servers/doc_summarizer/ ./mcp_servers/doc_summarizer/

EXPOSE 8003

CMD ["python", "-m", "mcp_servers.doc_summarizer.server"]
