import json
import re
import shutil
import unicodedata
import zlib
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx2
import numpy as np
import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from assistant.company import Company, load_company
from assistant.config import Settings
from assistant.main import create_app
from assistant.notifications import Recipient
from assistant.rag.embeddings import Vectors, normalize
from assistant.sandbox_client import SandboxClient
from assistant.servicenow_client import ServiceNowClient
from sandbox_runner.app import create_app as create_sandbox_app
from sandbox_runner.executor import Executor
from servicenow_mock.main import MockSettings
from servicenow_mock.main import create_app as create_servicenow_mock

REPO = Path(__file__).resolve().parents[2]
EMPRESA_DIR = REPO / "empresa"
SERVICENOW_SECRET = "secreto-de-servicenow-de-prueba"
DIRECTORY_SECRET = "secreto-del-directorio-de-prueba-" + "0" * 16
ROLE_TOKENS = {
    role: f"{role}-token-de-prueba-{'0' * 32}" for role in ("ventas", "rh", "empleado", "gerente")
}

AppFactory = Callable[[Settings], FastAPI]


class HashingEmbedder:
    """Embedder determinista sin modelo: cada palabra suma 1 en una posición fija del vector."""

    model_name = "hashing-test"

    def __init__(self, dimension: int = 512) -> None:
        self.dimension = dimension
        self.calls = 0

    def _vector(self, text: str) -> Vectors:
        plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
        vector = np.zeros(self.dimension, dtype=np.float32)
        for word in re.findall(r"\w+", plain):
            vector[zlib.crc32(word.encode()) % self.dimension] += 1
        return vector

    async def embed_documents(self, texts: Sequence[str]) -> Vectors:
        self.calls += 1
        return normalize(np.array([self._vector(t) for t in texts]))

    async def embed_query(self, text: str) -> Vectors:
        query: Vectors = normalize(self._vector(text))[0]
        return query


class RecordingSender:
    """Guarda los avisos en vez de enviarlos: los tests nunca tocan Telegram."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []  # (token del bot, chat, texto)
        self.fail = False
        self.closed = False

    async def send(self, recipient: Recipient, text: str) -> bool:
        if self.fail:
            raise RuntimeError(f"sin red: {recipient.bot_token.get_secret_value()}")
        self.sent.append((recipient.bot_token.get_secret_value(), recipient.chat_id, text))
        return True

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def empresa_dir() -> Path:
    return EMPRESA_DIR


@pytest.fixture
def empresa_copy(tmp_path: Path) -> Path:
    """Copia editable de empresa/ para probar configuraciones inválidas."""
    destination = tmp_path / "empresa"
    shutil.copytree(EMPRESA_DIR, destination)
    return destination


@pytest.fixture
def repo_copy(tmp_path: Path) -> Path:
    """Copia de lo que tocan las herramientas de administración (sin .env ni código)."""
    destination = tmp_path / "repo"
    for folder in ("empresa", "openclaw"):
        shutil.copytree(REPO / folder, destination / folder, ignore=shutil.ignore_patterns("local"))
    for name in (".env.example", "docker-compose.yml"):
        shutil.copyfile(REPO / name, destination / name)
    return destination


def parse_json5(text: str) -> Any:
    """Lector mínimo del JSON5 de OpenClaw: comentarios, claves sin comillas y comas finales."""
    text = re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+//[^\"\n]*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"([{,]\s*)([A-Za-z_][\w-]*)\s*:", r'\1"\2":', text)
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return json.loads(text)


@pytest.fixture
def company(empresa_dir: Path) -> Company:
    return load_company(empresa_dir)


@pytest.fixture
def role_tokens() -> dict[str, SecretStr]:
    return {role: SecretStr(token) for role, token in ROLE_TOKENS.items()}


@pytest.fixture
def settings(empresa_dir: Path, role_tokens: dict[str, SecretStr]) -> Settings:
    return Settings(empresa_dir=empresa_dir, role_tokens=role_tokens)


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder()


@pytest.fixture
def fake_redis() -> FakeAsyncRedis:
    return FakeAsyncRedis(server=FakeServer())


@pytest.fixture
def servicenow() -> ServiceNowClient:
    """Cliente real conectado al ServiceNow simulado, en memoria (sin red)."""
    mock = create_servicenow_mock(
        MockSettings(servicenow_client_secret=SecretStr(SERVICENOW_SECRET)),
        redis=FakeAsyncRedis(server=FakeServer()),
    )
    http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=mock))
    return ServiceNowClient(
        http, "http://servicenow.test", "cardinal-assistant", SecretStr(SERVICENOW_SECRET)
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> SandboxClient:
    """Cliente real conectado al runner real (procesos hijos de verdad), en memoria."""
    runner = create_sandbox_app(Executor(work_root=tmp_path, kill_strays=False))
    transport = httpx2.ASGITransport(app=runner)
    return SandboxClient(httpx2.AsyncClient(transport=transport, base_url="http://sandbox"))


@pytest.fixture
def notices() -> RecordingSender:
    return RecordingSender()


@pytest.fixture
def make_app(
    fake_redis: FakeAsyncRedis,
    embedder: HashingEmbedder,
    servicenow: ServiceNowClient,
    sandbox: SandboxClient,
    notices: RecordingSender,
) -> AppFactory:
    """Crea la app con Redis, embeddings y ServiceNow locales: sin red ni modelos."""
    return lambda settings: create_app(
        settings,
        redis=fake_redis,
        embedder=embedder,
        servicenow=servicenow,
        sandbox=sandbox,
        notices=notices,
    )


@pytest.fixture
def app(make_app: AppFactory, settings: Settings) -> FastAPI:
    return make_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> Callable[[str], dict[str, str]]:
    return lambda role: {"Authorization": f"Bearer {ROLE_TOKENS[role]}"}
