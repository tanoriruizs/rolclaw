import asyncio
import os
import signal
from pathlib import Path

import pytest

from sandbox_runner.executor import Executor, Limits, kill_group, kill_stray_processes


@pytest.fixture
def executor(tmp_path: Path) -> Executor:
    # kill_strays=False: limpiar procesos solo es seguro dentro del contenedor.
    return Executor(work_root=tmp_path, kill_strays=False)


def _with(tmp_path: Path, **limits: float) -> Executor:
    return Executor(Limits(**limits), work_root=tmp_path, kill_strays=False)  # type: ignore[arg-type]


# --- Cálculos normales ---------------------------------------------------------------


async def test_demo_quote_total(executor: Executor) -> None:
    result = await executor.run("50 * 15000 * 0.85")

    assert result.resultado == "637500.0"
    assert result.error is None


async def test_demo_business_days_with_the_standard_library(executor: Executor) -> None:
    code = (
        "from datetime import date, timedelta\n"
        "inicio, fin = date(2026, 12, 14), date(2026, 12, 18)\n"
        "sum(1 for i in range((fin - inicio).days + 1)\n"
        "    if (inicio + timedelta(i)).weekday() < 5)"
    )

    assert (await executor.run(code)).resultado == "5"


async def test_prints_and_last_expression(executor: Executor) -> None:
    result = await executor.run("print('hola')\nx = 2\nx * 3")

    assert (result.stdout, result.resultado) == ("hola\n", "6")


async def test_code_without_final_expression_has_no_result(executor: Executor) -> None:
    result = await executor.run("x = 1")

    assert result.resultado is None
    assert result.error is None


@pytest.mark.parametrize(
    ("code", "error"),
    [
        ("1 +", "SyntaxError"),
        ("1 / 0", "ZeroDivisionError: division by zero"),
        ("import requests", "ModuleNotFoundError"),
        ("import os\nos._exit(3)", "El cálculo terminó de forma inesperada."),
    ],
)
async def test_errors_are_reported_without_tracebacks(
    executor: Executor, code: str, error: str
) -> None:
    result = await executor.run(code)

    assert result.error is not None
    assert result.error.startswith(error)
    assert "Traceback" not in result.error


# --- Intentos de abuso ---------------------------------------------------------------


async def test_infinite_loop_is_stopped_by_the_wall_clock(tmp_path: Path) -> None:
    result = await _with(tmp_path, wall_seconds=1, cpu_seconds=30).run("while True:\n    pass")

    assert result.error == "Tiempo agotado: el cálculo superó 1 s."


async def test_sleeping_forever_is_stopped_too(tmp_path: Path) -> None:
    result = await _with(tmp_path, wall_seconds=1).run("import time\ntime.sleep(60)")

    assert result.error == "Tiempo agotado: el cálculo superó 1 s."
    assert result.duracion_ms < 5000


async def test_cpu_limit_kills_busy_code(tmp_path: Path) -> None:
    result = await _with(tmp_path, cpu_seconds=1, wall_seconds=20).run("while True:\n    pass")

    assert result.error == "Se excedió el límite de CPU."


async def test_memory_bomb_fails_with_memory_error(executor: Executor) -> None:
    result = await executor.run("bloque = bytearray(1024 ** 3)")

    assert result.error == "MemoryError: se excedió el límite de memoria"


async def test_huge_files_are_cut_by_the_file_size_limit(executor: Executor) -> None:
    result = await executor.run("open('grande.bin', 'wb').write(b'0' * 5_000_000)")

    assert result.error == "OSError: [Errno 27] File too large"


async def test_long_output_is_truncated(tmp_path: Path) -> None:
    result = await _with(tmp_path, max_output_chars=100).run("print('x' * 1000)")

    assert result.truncado is True
    assert len(result.stdout) == 100


async def test_child_does_not_inherit_secrets(
    executor: Executor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROLE_TOKEN_VENTAS", "secreto-que-no-debe-verse")

    result = await executor.run("import os\nsorted(os.environ)")

    assert result.resultado is not None
    assert "ROLE_TOKEN_VENTAS" not in result.resultado


async def test_limits_cannot_be_raised_by_user_code(executor: Executor) -> None:
    code = "import resource\nresource.setrlimit(resource.RLIMIT_CPU, (999, 999))"

    result = await executor.run(code)

    assert result.error is not None
    assert result.error.startswith("ValueError")


async def test_oversized_code_is_rejected_without_running(tmp_path: Path) -> None:
    result = await _with(tmp_path, max_code_chars=10).run("x = 1 + 1 + 1 + 1")

    assert result.error == "El código excede 10 caracteres."


async def test_calculations_run_one_at_a_time(executor: Executor) -> None:
    results = await asyncio.gather(executor.run("1 + 1"), executor.run("2 + 2"))

    assert [r.resultado for r in results] == ["2", "4"]


async def test_work_directory_is_deleted(tmp_path: Path) -> None:
    await _with(tmp_path).run("open('rastro.txt', 'w').write('x')")

    assert await asyncio.to_thread(lambda: list(tmp_path.iterdir())) == []


# --- Limpieza de procesos sueltos (con un /proc falso: nunca mata procesos reales) ---


def test_kill_stray_processes_spares_pid_1_and_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ["1", str(os.getpid()), "4242", "4243", "self", "cpuinfo"]:
        (tmp_path / name).mkdir()
    sent: list[tuple[int, int]] = []

    def fake_kill(pid: int, sig: int) -> None:
        if pid == 4243:
            raise ProcessLookupError
        sent.append((pid, sig))

    monkeypatch.setattr(os, "kill", fake_kill)

    assert kill_stray_processes(tmp_path) == [4242]
    assert sent == [(4242, signal.SIGKILL)]


async def test_strays_are_cleaned_after_each_run_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_cleanup() -> list[int]:
        calls.append("limpieza")
        return []

    monkeypatch.setattr("sandbox_runner.executor.kill_stray_processes", fake_cleanup)

    await Executor(work_root=tmp_path, kill_strays=True).run("1")

    assert calls == ["limpieza"]


def test_kill_group_ignores_processes_that_already_ended() -> None:
    kill_group(2**22 + 12345)  # un pid que no existe: no debe lanzar error
