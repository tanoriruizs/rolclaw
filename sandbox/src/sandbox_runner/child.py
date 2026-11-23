"""Proceso hijo: se impone límites y después ejecuta el código recibido por stdin.

Corre con `python -I -S child.py '<límites JSON>'`, en un directorio temporal vacío.
La última expresión del código es el resultado (como en una calculadora o un notebook).
Solo usa la biblioteca estándar: no importa nada del proyecto.
"""

import ast
import json
import resource
import sys
from pathlib import Path


def apply_limits(limits: dict[str, int]) -> None:
    # El código del usuario no puede subir un límite duro.
    for name, value in limits.items():
        # En CPU el duro va 1 s después: así llega primero SIGXCPU (aviso claro) y no SIGKILL.
        hard = value + 1 if name == "RLIMIT_CPU" else value
        resource.setrlimit(getattr(resource, name), (value, hard))


def run(code: str) -> None:
    tree = ast.parse(code, "<calculo>", "exec")
    last = tree.body[-1] if tree.body else None
    if isinstance(last, ast.Expr):
        tree.body.pop()
    else:
        last = None
    namespace: dict[str, object] = {"__name__": "__calculo__"}
    # Ejecutar código arbitrario es justo el propósito de este proceso aislado.
    exec(compile(tree, "<calculo>", "exec"), namespace)  # noqa: S102
    if last is not None:
        expression = ast.Expression(last.value)
        value = eval(compile(expression, "<calculo>", "eval"), namespace)  # noqa: S307
        Path("resultado.txt").write_text(repr(value), encoding="utf-8")


def main() -> None:
    apply_limits(json.loads(sys.argv[1]))
    try:
        run(sys.stdin.read())
    except MemoryError:
        Path("error.txt").write_text("MemoryError: se excedió el límite de memoria", "utf-8")
        sys.exit(1)
    except BaseException as exc:  # cualquier error del código del usuario se reporta
        Path("error.txt").write_text(f"{type(exc).__name__}: {exc}", encoding="utf-8")
        sys.exit(1)


if __name__ == "__main__":
    main()
