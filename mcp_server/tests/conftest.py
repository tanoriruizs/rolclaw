import shutil
from pathlib import Path

import pytest

EMPRESA_DIR = Path(__file__).resolve().parents[2] / "empresa"


@pytest.fixture
def empresa_dir() -> Path:
    return EMPRESA_DIR


@pytest.fixture
def empresa_copy(tmp_path: Path) -> Path:
    """Copia editable de empresa/ para probar configuraciones inválidas."""
    destination = tmp_path / "empresa"
    shutil.copytree(EMPRESA_DIR, destination)
    return destination
