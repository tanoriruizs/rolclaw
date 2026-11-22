import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from assistant.company import Company, load_company
from assistant.config import Settings
from assistant.main import create_app

EMPRESA_DIR = Path(__file__).resolve().parents[2] / "empresa"
ROLE_TOKENS = {
    role: f"{role}-token-de-prueba-{'0' * 32}" for role in ("ventas", "rh", "empleado", "gerente")
}


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
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> Callable[[str], dict[str, str]]:
    return lambda role: {"Authorization": f"Bearer {ROLE_TOKENS[role]}"}
