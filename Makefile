.DEFAULT_GOAL := help
UV := uv

.PHONY: help setup tokens lint format test secrets ci

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Instala dependencias y los hooks de pre-commit
	$(UV) sync --all-packages
	$(UV) run pre-commit install

tokens: ## Genera tokens de rol y secretos locales en .env
	$(UV) run python scripts/gen_tokens.py

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
