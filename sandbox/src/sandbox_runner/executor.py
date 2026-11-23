"""Ejecuta cada cálculo en un proceso hijo con límites, tiempo máximo y salida acotada."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

CHILD_SCRIPT = Path(__file__).with_name("child.py")
# El hijo no hereda variables de entorno: así no puede leer secretos del padre.
CHILD_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


@dataclass(frozen=True, slots=True)
class Limits:
    cpu_seconds: int = 2
    memory_bytes: int = 256 * 1024 * 1024
    max_processes: int = 32
    max_file_bytes: int = 1024 * 1024
    max_open_files: int = 32
    wall_seconds: float = 5.0
    max_output_chars: int = 10_000
    max_code_chars: int = 10_000

    def as_rlimits(self) -> dict[str, int]:
        return {
            "RLIMIT_CPU": self.cpu_seconds,
            "RLIMIT_AS": self.memory_bytes,
            "RLIMIT_NPROC": self.max_processes,
            "RLIMIT_FSIZE": self.max_file_bytes,
            "RLIMIT_NOFILE": self.max_open_files,
            "RLIMIT_CORE": 0,
        }


class ExecutionResult(BaseModel):
    stdout: str
    resultado: str | None
    error: str | None
    duracion_ms: int
    truncado: bool


# Python ignora SIGXFSZ (un archivo demasiado grande llega como OSError), así que solo CPU.
CPU_LIMIT_SIGNAL = -signal.SIGXCPU


class Executor:
    """Un cálculo a la vez: así es seguro limpiar procesos sueltos al terminar cada uno."""

    def __init__(
        self, limits: Limits | None = None, *, work_root: Path | None = None, kill_strays: bool
    ) -> None:
        self.limits = limits or Limits()
        self._work_root = work_root
        self._kill_strays = kill_strays
        self._lock = asyncio.Lock()

    async def run(self, code: str) -> ExecutionResult:
        if len(code) > self.limits.max_code_chars:
            return _failure(f"El código excede {self.limits.max_code_chars} caracteres.")
        async with self._lock:
            with tempfile.TemporaryDirectory(dir=self._work_root) as work:
                try:
                    return await self._run_in(Path(work), code)
                finally:
                    if self._kill_strays:
                        kill_stray_processes()

    async def _run_in(self, work: Path, code: str) -> ExecutionResult:
        start = time.monotonic()
        stdout_path, stderr_path = work / "stdout.txt", work / "stderr.txt"
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                "-S",
                str(CHILD_SCRIPT),
                json.dumps(self.limits.as_rlimits()),
                stdin=asyncio.subprocess.PIPE,
                stdout=stdout,
                stderr=stderr,
                cwd=work,
                env=CHILD_ENV,
                start_new_session=True,  # grupo propio: se puede matar con todos sus hijos
            )
            timed_out = False
            try:
                await asyncio.wait_for(process.communicate(code.encode()), self.limits.wall_seconds)
            except TimeoutError:
                timed_out = True
                kill_group(process.pid)
                await process.wait()

        output, truncated = _read_capped(stdout_path, self.limits.max_output_chars)
        return ExecutionResult(
            stdout=output,
            resultado=_read_optional(work / "resultado.txt", self.limits.max_output_chars),
            error=self._error(timed_out, process.returncode, work),
            duracion_ms=round((time.monotonic() - start) * 1000),
            truncado=truncated,
        )

    def _error(self, timed_out: bool, returncode: int | None, work: Path) -> str | None:
        if timed_out:
            return f"Tiempo agotado: el cálculo superó {self.limits.wall_seconds:g} s."
        reported = _read_optional(work / "error.txt", self.limits.max_output_chars)
        if reported:
            return reported
        if returncode == CPU_LIMIT_SIGNAL:
            return "Se excedió el límite de CPU."
        if returncode != 0:
            return "El cálculo terminó de forma inesperada."
        return None


def kill_group(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):  # ya había terminado
        os.killpg(pid, signal.SIGKILL)


def kill_stray_processes(proc_dir: Path = Path("/proc")) -> list[int]:
    """Mata todo proceso que no sea este runner (solo dentro del contenedor del sandbox).

    Un código malicioso puede escapar de su grupo con setsid() y quedarse dormido;
    como los cálculos van de uno en uno, al terminar no debería quedar nada vivo.
    PID 1 es tini (init: true en compose), que recoge los procesos que mueren.
    """
    own = {1, os.getpid(), os.getppid()}
    killed: list[int] = []
    for entry in proc_dir.iterdir():
        if entry.name.isdigit() and int(entry.name) not in own:
            try:
                os.kill(int(entry.name), signal.SIGKILL)
                killed.append(int(entry.name))
            except (ProcessLookupError, PermissionError):
                pass
    return killed


def _read_capped(path: Path, limit: int) -> tuple[str, bool]:
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    text = data[:limit].decode("utf-8", errors="replace")
    return text, len(data) > limit


def _read_optional(path: Path, limit: int) -> str | None:
    if not path.exists():
        return None
    return _read_capped(path, limit)[0]


def _failure(message: str) -> ExecutionResult:
    return ExecutionResult(stdout="", resultado=None, error=message, duracion_ms=0, truncado=False)
