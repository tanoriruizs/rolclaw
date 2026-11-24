.DEFAULT_GOAL := help
UV := uv
COMPOSE := docker compose

.PHONY: help setup tokens validate new-role openclaw-config directorio check-env up reload pairing demo-reset \
	down logs ps ingest sandbox-check run lint format test secrets ci

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Instala dependencias y los hooks de pre-commit (para desarrollar)
	$(UV) sync --all-packages
	$(UV) run pre-commit install

tokens: ## Genera tokens de rol y secretos locales en .env (solo necesita python3)
	python3 scripts/gen_tokens.py

validate: ## Revisa empresa/ y lo que depende de ella, sin levantar nada
	$(UV) run python -m assistant.admin validar

new-role: ## Crea un rol: make new-role ROL=compras NOMBRE="Compras" PERSONA="Carla Ríos"
	$(UV) run python -m assistant.admin nuevo-rol "$(ROL)" --nombre "$(NOMBRE)" --persona "$(PERSONA)"

openclaw-config: ## Regenera openclaw/openclaw.json5 a partir de empresa/
	$(UV) run python -m assistant.admin openclaw

directorio: ## Modo directorio: prepara un solo bot con un agente por persona
	$(UV) run --env-file .env python -m assistant.admin directorio

check-env:
	@python3 scripts/check_env.py

up: check-env ## Construye y levanta los servicios; espera a que estén sanos
	$(COMPOSE) up -d --build --wait redis servicenow-mock sandbox mcp-server
	@# OpenClaw copia su config y sus agentes del repo al arrancar: se recrea para aplicar cambios.
	$(COMPOSE) up -d --wait --force-recreate --no-deps openclaw

reload: ## Aplica cambios en empresa/ (reinicia el MCP server; re-indexa si hace falta)
	$(COMPOSE) restart mcp-server
	$(COMPOSE) up -d --wait mcp-server

pairing: ## Aprueba el pairing de Telegram solo para TELEGRAM_OWNER_ID (ver .env)
	@$(COMPOSE) exec -T openclaw sh /config-src/approve_pairing.sh \
		"$$(grep -E '^TELEGRAM_OWNER_ID=' .env | cut -d= -f2)"

demo-reset: ## Borra solicitudes y auditoría para grabar la demo desde REQ0010001
	@# Claves: sn:sc_request* (ServiceNow simulado, Redis db 1) y audit:events (db 0).
	@$(COMPOSE) exec -T redis sh -c \
		"redis-cli -n 1 --scan --pattern 'sn:sc_request*' | xargs -r redis-cli -n 1 del; \
		redis-cli -n 0 del audit:events" >/dev/null
	@echo "Demo en blanco: la siguiente solicitud será REQ0010001."

down: ## Detiene los servicios (los datos de Redis se conservan)
	$(COMPOSE) down

logs: ## Sigue los logs de todos los servicios
	$(COMPOSE) logs -f

ps: ## Estado y salud de los servicios
	$(COMPOSE) ps

ingest: ## Re-indexa los documentos de empresa/ dentro del contenedor
	$(COMPOSE) exec mcp-server python -m assistant.rag.ingest

sandbox-check: ## Lanza intentos de escape contra el sandbox real
	$(COMPOSE) exec -T mcp-server python -m assistant.sandbox_check

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

ci: lint test validate secrets ## Lo mismo que corre GitHub Actions
