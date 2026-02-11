.PHONY: setup lint test docker-build docker-up docker-down demo seed clean help

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

setup: ## Create venv and install all dependencies
	python3 -m venv venv
	venv/bin/pip install --upgrade pip
	venv/bin/pip install -r requirements.txt
	venv/bin/pip install pydantic-settings langchain-ollama langgraph
	venv/bin/pip install pytest pytest-asyncio ruff black
	@echo "\n  Activate with: source venv/bin/activate"

lint: ## Run ruff and black checks
	ruff check .
	black --check .

lint-fix: ## Auto-fix lint issues
	ruff check --fix .
	black .

test: ## Run unit tests (skip integration)
	pytest tests/ -v -m "not integration"

test-all: ## Run all tests including integration
	pytest tests/ -v

test-ci: ## Run tests with short output (CI mode)
	pytest tests/ -m "not integration" --tb=short -q

docker-build: ## Build all Docker images
	docker compose build

docker-up: ## Start all services
	docker compose up --build -d
	@echo "\n  Waiting for services..."
	@sleep 5
	docker compose ps

docker-down: ## Stop all services
	docker compose down

demo: ## Run the dynamic tool discovery demo
	python scripts/demo.py

seed: ## Seed realistic data for screenshots
	python scripts/seed_data.py

clean: ## Remove containers, volumes, and cache files
	docker compose down -v --remove-orphans
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -f notes_data.json
	@echo "  Cleaned."
