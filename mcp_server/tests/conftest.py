import shutil
from pathlib import Path

import pytest
from pydantic import SecretStr

from assistant.company import Company, load_company

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
