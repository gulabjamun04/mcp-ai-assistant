FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir mcp

# Copy package structure for relative imports
COPY mcp_servers/__init__.py ./mcp_servers/__init__.py
COPY mcp_servers/calculator/ ./mcp_servers/calculator/

EXPOSE 8004

CMD ["python", "-m", "mcp_servers.calculator.server"]
