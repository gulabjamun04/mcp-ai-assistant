FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir mcp httpx beautifulsoup4 ddgs

# Copy package structure for relative imports
COPY mcp_servers/__init__.py ./mcp_servers/__init__.py
COPY mcp_servers/web_search/ ./mcp_servers/web_search/

EXPOSE 8002

CMD ["python", "-m", "mcp_servers.web_search.server"]
