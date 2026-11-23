"""Intentos de escape contra el sandbox real. Uso: make sandbox-check

Se ejecuta dentro del contenedor mcp-server y habla con el sandbox por el mismo socket
que usa la herramienta `calcular`. Termina con código 1 si algún ataque tuvo éxito.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass

from assistant.config import Settings
from assistant.sandbox_client import CalculationResult, SandboxClient


@dataclass(frozen=True, slots=True)
class Scenario:
    nombre: str
    codigo: str
    contenido: Callable[[CalculationResult], bool]


def _failed(result: CalculationResult) -> bool:
    return result.error is not None


def _no_secrets(result: CalculationResult) -> bool:
    names = result.resultado or ""
    return not any(word in names for word in ("TOKEN", "SECRET", "KEY", "REDIS"))


def _no_leftover_processes(result: CalculationResult) -> bool:
    # Solo deben vivir tini (pid 1), el runner, este cálculo y, a lo mucho, un healthcheck.
    return len(ast.literal_eval(result.resultado or "[]")) <= 4


SCENARIOS = [
    Scenario("cálculo normal", "50 * 15000 * 0.85", lambda r: r.resultado == "637500.0"),
    Scenario(
        "sin internet",
        "import socket\nsocket.create_connection(('1.1.1.1', 443), timeout=2)",
        _failed,
    ),
    Scenario(
        "sin acceso a otros servicios",
        "import socket\nsocket.create_connection(('redis', 6379), timeout=2)",
        _failed,
    ),
    Scenario(
        "disco de solo lectura",
        "open('/home/sandbox/escape.txt', 'w').write('x')",
        lambda r: r.error is not None and "Read-only file system" in r.error,
    ),
    Scenario("sin secretos en el entorno", "import os\nsorted(os.environ)", _no_secrets),
    Scenario("bomba de memoria", "b = bytearray(1024 ** 3)", _failed),
    Scenario("bucle infinito", "while True:\n    pass", _failed),
    Scenario("bomba de procesos", "import os\nwhile True:\n    os.fork()", _failed),
    Scenario(
        "proceso escondido con setsid",
        "import os, time\nif os.fork() == 0:\n    os.setsid()\n    time.sleep(600)\n'listo'",
        lambda r: True,  # el ataque "funciona"; lo que importa es el siguiente escenario
    ),
    Scenario(
        "no quedan procesos de ataques",
        "import os\nsorted(int(p) for p in os.listdir('/proc') if p.isdigit())",
        _no_leftover_processes,
    ),
    Scenario("el sandbox sigue respondiendo", "1 + 1", lambda r: r.resultado == "2"),
]


async def run_checks(client: SandboxClient) -> list[tuple[str, bool, str]]:
    report = []
    for scenario in SCENARIOS:
        result = await client.run(scenario.codigo)
        detail = result.error or result.resultado or ""
        report.append((scenario.nombre, scenario.contenido(result), detail[:70]))
    return report


async def _main() -> int:
    from assistant.container import create_sandbox

    client = create_sandbox(Settings())
    try:
        report = await run_checks(client)
    finally:
        await client.aclose()
    for name, contained, detail in report:
        print(f"{'OK ' if contained else 'FALLA'}  {name:32} {detail}")
    failures = sum(not contained for _, contained, _ in report)
    print(f"\n{len(report) - failures}/{len(report)} escenarios contenidos")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
