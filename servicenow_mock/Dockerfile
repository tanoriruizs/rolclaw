# syntax=docker/dockerfile:1
# Se construye desde la raíz del repo: docker build -f servicenow_mock/Dockerfile .

FROM python:3.13.18-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY mcp_server/pyproject.toml mcp_server/
COPY servicenow_mock/pyproject.toml servicenow_mock/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package servicenow-mock --no-install-workspace

COPY servicenow_mock/src servicenow_mock/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package servicenow-mock --no-editable


FROM python:3.13.18-slim-trixie
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER app
EXPOSE 8100
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:8100/health').status != 200)"]
CMD ["uvicorn", "servicenow_mock.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8100"]
