import re
import shutil
import unicodedata
import zlib
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import numpy as np
import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from assistant.company import Company, load_company
from assistant.config import Settings
from assistant.main import create_app
from assistant.rag.embeddings import Vectors, normalize

EMPRESA_DIR = Path(__file__).resolve().parents[2] / "empresa"
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
def make_app(fake_redis: FakeAsyncRedis, embedder: HashingEmbedder) -> AppFactory:
    """Crea la app con Redis y embeddings falsos: sin red, sin descargar modelos."""
    return lambda settings: create_app(settings, redis=fake_redis, embedder=embedder)


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
