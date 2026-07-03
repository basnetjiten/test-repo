SHELL := /bin/bash

.PHONY: init up up-proxy up-ollama down logs ps health smoke clean-data pull-image examples

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

init:
	@if [[ ! -f .env ]]; then cp .env.example .env; echo "Created .env from template — add your OpenAI API key before starting."; fi
	@mkdir -p data/rag_storage data/inputs data/prompts

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

up:
	docker compose up -d

up-proxy:
	docker compose --profile proxy up -d

up-ollama:
	docker compose --profile local-llm up -d

down:
	docker compose down

# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------

logs:
	docker compose logs -f lightrag

ps:
	docker compose ps

health:
	@curl -sS http://localhost:$${PORT:-9621}/health | python3 -m json.tool

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

smoke:
	bash ./scripts/smoke-test.sh

examples:
	@echo "Installing example dependencies..."
	pip install requests httpx python-dotenv --quiet
	@echo "Running ingestion example..."
	cd examples && python 01_ingest_text.py
	@echo "Running query example..."
	cd examples && python 02_query_modes.py

# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

pull-image:
	docker pull $$(grep LIGHTRAG_IMAGE .env | cut -d= -f2)

# WARNING: deletes all indexed data. Re-index documents after this.
clean-data:
	@echo "WARNING: This will delete all LightRAG indexed data in ./data/rag_storage"
	@read -p "Type 'yes' to confirm: " confirm && [[ "$$confirm" == "yes" ]] || exit 1
	rm -rf data/rag_storage data/inputs
	mkdir -p data/rag_storage data/inputs data/prompts
	@echo "Done. Start fresh with: make up"

# ---------------------------------------------------------------------------
# BFF Development & Build
# ---------------------------------------------------------------------------

run-bff:
	@echo "Starting custom BFF server locally..."
	PYTHONPATH=. python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8088 --reload

build-bff:
	docker compose build backend

