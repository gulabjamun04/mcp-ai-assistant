#!/usr/bin/env bash
# Pull the Ollama model into the Docker container.
# Run after the Ollama container is healthy:
#   ./scripts/pull_model.sh
#
# Uses qwen3:1.7b by default (fits in Docker's memory limit).
# Set MODEL env var to override: MODEL=qwen3:8b ./scripts/pull_model.sh

set -euo pipefail

MODEL="${MODEL:-qwen3:1.7b}"

echo "Pulling ${MODEL} model into mcp-ollama container..."
docker exec mcp-ollama ollama pull "${MODEL}"
echo "Done. Model ${MODEL} is ready."
