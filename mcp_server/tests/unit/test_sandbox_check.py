import pytest

from assistant import sandbox_check
from assistant.sandbox_check import SCENARIOS, run_checks
from assistant.sandbox_client import CalculationResult


def _result(resultado: str | None = None, error: str | None = None) -> CalculationResult:
    return CalculationResult(
        stdout="", resultado=resultado, error=error, duracion_ms=1, truncado=False
    )


CONTAINED = {
    "cálculo normal": _result("637500.0"),
    "sin internet": _result(error="OSError: [Errno 101] Network is unreachable"),
    "sin acceso a otros servicios": _result(error="gaierror: [Errno -3] Temporary failure"),
    "disco de solo lectura": _result(error="OSError: [Errno 30] Read-only file system"),
    "sin secretos en el entorno": _result("['LANG', 'PATH']"),
    "bomba de memoria": _result(error="MemoryError: se excedió el límite de memoria"),
    "bucle infinito": _result(error="Tiempo agotado: el cálculo superó 5 s."),
    "bomba de procesos": _result(error="BlockingIOError: [Errno 11]"),
    "proceso escondido con setsid": _result("'listo'"),
    "no quedan procesos de ataques": _result("[1, 57]"),
    "el sandbox sigue respondiendo": _result("2"),
}


class FakeSandbox:
    def __init__(self, overrides: dict[str, CalculationResult] | None = None) -> None:
        by_name = CONTAINED | (overrides or {})
        self._by_code = {s.codigo: by_name[s.nombre] for s in SCENARIOS}
        self.closed = False

    async def run(self, code: str) -> CalculationResult:
        return self._by_code[code]

    async def aclose(self) -> None:
        self.closed = True


async def test_every_scenario_passes_when_the_sandbox_contains_it() -> None:
    report = await run_checks(FakeSandbox())  # type: ignore[arg-type]

    assert all(contained for _, contained, _ in report)
    assert len(report) == len(SCENARIOS)


@pytest.mark.parametrize(
    ("name", "leak"),
    [
        ("sin internet", _result("<socket conectado>")),
        ("sin secretos en el entorno", _result("['PATH', 'ROLE_TOKEN_VENTAS']")),
        ("no quedan procesos de ataques", _result("[1, 7, 8, 9, 10]")),
        ("disco de solo lectura", _result(error="PermissionError: denied")),
    ],
)
async def test_a_successful_attack_is_reported(name: str, leak: CalculationResult) -> None:
    fake = FakeSandbox({name: leak})

    report = {n: ok for n, ok, _ in await run_checks(fake)}  # type: ignore[arg-type]

    assert report[name] is False


@pytest.mark.parametrize(
    ("overrides", "exit_code"), [({}, 0), ({"bucle infinito": _result("1")}, 1)]
)
async def test_main_prints_a_report_and_sets_the_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    overrides: dict[str, CalculationResult],
    exit_code: int,
) -> None:
    fake = FakeSandbox(overrides)
    monkeypatch.setattr("assistant.container.create_sandbox", lambda _: fake)

    assert await sandbox_check._main() == exit_code
    assert fake.closed
    assert "escenarios contenidos" in capsys.readouterr().out
