.DEFAULT_GOAL := help
UV := uv
COMPOSE := docker compose

.PHONY: help setup tokens up down logs ps ingest run lint format test secrets ci

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Instala dependencias y los hooks de pre-commit
	$(UV) sync --all-packages
	$(UV) run pre-commit install

tokens: ## Genera tokens de rol y secretos locales en .env
	$(UV) run python scripts/gen_tokens.py

up: ## Construye y levanta los servicios; espera a que estén sanos
	$(COMPOSE) up -d --build --wait

down: ## Detiene los servicios (los datos de Redis se conservan)
	$(COMPOSE) down

logs: ## Sigue los logs de todos los servicios
	$(COMPOSE) logs -f

ps: ## Estado y salud de los servicios
	$(COMPOSE) ps

ingest: ## Re-indexa los documentos de empresa/ dentro del contenedor
	$(COMPOSE) exec mcp-server python -m assistant.rag.ingest

run: ## Servidor local con recarga automática (necesita un Redis en localhost:6379)
	$(UV) run --env-file .env uvicorn assistant.main:create_app --factory --reload \
		--host 127.0.0.1 --port 8000

lint: ## ruff (lint + formato) y mypy --strict
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy

format: ## Aplica formato y arreglos automáticos de ruff
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

test: ## Tests con cobertura (mínimo 80 %)
	$(UV) run pytest --cov --cov-report=term-missing

secrets: ## Busca secretos en todo el repo con gitleaks
	$(UV) run pre-commit run gitleaks --all-files

ci: lint test secrets ## Lo mismo que corre GitHub Actions
