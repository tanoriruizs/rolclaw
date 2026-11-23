# syntax=docker/dockerfile:1
# Se construye desde la raíz del repo: docker build -f mcp_server/Dockerfile .

FROM python:3.13.18-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Primero solo las dependencias: esta capa se reutiliza mientras uv.lock no cambie.
COPY pyproject.toml uv.lock ./
COPY mcp_server/pyproject.toml mcp_server/
COPY servicenow_mock/pyproject.toml servicenow_mock/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package assistant --no-install-workspace

COPY mcp_server/src mcp_server/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package assistant --no-editable

# El modelo se descarga al construir: el contenedor arranca sin internet.
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN .venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('${EMBEDDING_MODEL}', cache_dir='/models')" \
    && chmod -R a+rX /models


FROM python:3.13.18-slim-trixie
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /models /models
COPY empresa /app/empresa

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    EMPRESA_DIR=/app/empresa \
    EMBEDDING_CACHE_DIR=/models \
    HF_HUB_OFFLINE=1

USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8000/ready').status != 200)"]
CMD ["uvicorn", "assistant.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
