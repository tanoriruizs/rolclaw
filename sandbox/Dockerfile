# syntax=docker/dockerfile:1
# Se construye desde la raíz del repo: docker build -f sandbox/Dockerfile .

FROM python:3.13.18-slim-trixie AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY mcp_server/pyproject.toml mcp_server/
COPY sandbox/pyproject.toml sandbox/
COPY servicenow_mock/pyproject.toml servicenow_mock/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package sandbox-runner --no-install-workspace

COPY sandbox/src sandbox/src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package sandbox-runner --no-editable


FROM python:3.13.18-slim-trixie
# uid distinto al de los demás servicios: el límite de procesos (RLIMIT_NPROC) es por usuario.
RUN useradd --create-home --uid 10002 sandbox \
    && mkdir -p /run/sandbox && chown sandbox:sandbox /run/sandbox
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SANDBOX_KILL_STRAYS=1 \
    SANDBOX_SOCKET=/run/sandbox/sandbox.sock
USER sandbox
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD ["python", "-m", "sandbox_runner.health"]
CMD ["uvicorn", "sandbox_runner.app:create_app", "--factory", "--uds", "/run/sandbox/sandbox.sock"]
