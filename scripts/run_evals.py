"""Evals de los agentes: conversa con cada agente y juzga por lo que pasó EN EL SERVIDOR.

No se califica lo bien que redacta el modelo, sino los hechos: qué solicitudes existen, en
qué estado quedaron y qué herramientas se usaron (auditoría). Usa el modelo de verdad, así
que gasta créditos y necesita los servicios arriba (make up) en modo demo.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from check_env import parse_env

DEFAULT_SCENARIOS = Path("evals/escenarios.json")
DEFAULT_EXEC = "docker compose exec -T openclaw"
ROLE_TOKEN_PREFIX = "ROLE_TOKEN_"  # noqa: S105 - es un prefijo, no un secreto
AUDIT_TOOL = "ver_auditoria"
AUDIT_LIMIT = 200  # el máximo que entrega el servidor
# Separadores de miles: punto, coma, espacio y espacios duros.
_THOUSANDS = re.compile(rf"(?<=\d)[., {chr(0xA0)}{chr(0x202F)}](?=\d{{3}}(?!\d))")
# Resultados de auditoría que significan que la herramienta NO se ejecutó.
FAILED_OUTCOMES = ("denegado", "no_encontrado", "conflicto", "datos_invalidos", "servicio", "error")
EXPECT_KEYS = frozenset(
    {
        "solicitudes",
        "solicitudes_permitidas",
        "preparadas",
        "herramientas",
        "herramientas_prohibidas",
        "respuesta_contiene",
        "respuesta_no_contiene",
    }
)


class EvalError(Exception):
    """Las evals no se pueden correr; el mensaje dice qué corregir."""


class AgentError(Exception):
    """El agente no respondió; cuenta como fallo del escenario, no del arnés."""


@dataclass(frozen=True, slots=True)
class Scenario:
    id: str
    descripcion: str
    rol: str
    mensajes: tuple[str, ...]
    prepara: tuple[dict[str, Any], ...] = ()
    espera: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Reply:
    text: str
    cost: float = 0.0


@dataclass(slots=True)
class Result:
    scenario: Scenario
    failures: list[str] = field(default_factory=list)
    replies: list[str] = field(default_factory=list)
    cost: float = 0.0
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.failures


class Api(Protocol):
    """El servidor visto por un rol (REST con su token)."""

    def get(self, role: str, path: str) -> Any: ...

    def post(self, role: str, path: str, body: Mapping[str, Any]) -> Any: ...


class Agent(Protocol):
    def say(self, role: str, session: str, message: str) -> Reply: ...


# --- Escenarios --------------------------------------------------------------------------


def load_scenarios(path: Path) -> list[Scenario]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvalError(f"No existe {path}.") from None
    except json.JSONDecodeError as exc:
        raise EvalError(f"{path}: JSON inválido ({exc}).") from None
    items = data.get("escenarios") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise EvalError(f"{path}: debe tener una lista 'escenarios' con al menos uno.")
    scenarios = [
        _scenario(item, f"{path}: escenario {index + 1}") for index, item in enumerate(items)
    ]
    repeated = sorted({s.id for s in scenarios if [x.id for x in scenarios].count(s.id) > 1})
    if repeated:
        raise EvalError(f"{path}: ids repetidos: {', '.join(repeated)}.")
    return scenarios


def _scenario(item: object, where: str) -> Scenario:
    if not isinstance(item, dict):
        raise EvalError(f"{where}: debe ser un objeto.")
    unknown = sorted(set(item) - {"id", "descripcion", "rol", "mensajes", "prepara", "espera"})
    if unknown:
        raise EvalError(f"{where}: campos desconocidos: {', '.join(unknown)}.")
    for key in ("id", "descripcion", "rol"):
        if not isinstance(item.get(key), str) or not item[key].strip():
            raise EvalError(f"{where}: falta '{key}'.")
    messages = item.get("mensajes")
    if (
        not isinstance(messages, list)
        or not messages
        or not all(isinstance(m, str) for m in messages)
    ):
        raise EvalError(f"{where}: 'mensajes' debe ser una lista de textos.")
    prepare = item.get("prepara", [])
    expect = item.get("espera", {})
    if not isinstance(prepare, list) or not all(_is_request(p, with_role=True) for p in prepare):
        raise EvalError(f"{where}: cada elemento de 'prepara' necesita rol, flujo y datos.")
    if not isinstance(expect, dict) or set(expect) - EXPECT_KEYS:
        raise EvalError(f"{where}: 'espera' solo admite: {', '.join(sorted(EXPECT_KEYS))}.")
    if "solicitudes" in expect and "solicitudes_permitidas" in expect:
        raise EvalError(f"{where}: usa 'solicitudes' o 'solicitudes_permitidas', no las dos.")
    if len(expect.get("preparadas", [])) > len(prepare):
        raise EvalError(f"{where}: 'preparadas' tiene más elementos que 'prepara'.")
    return Scenario(
        id=item["id"],
        descripcion=item["descripcion"],
        rol=item["rol"],
        mensajes=tuple(messages),
        prepara=tuple(prepare),
        espera=expect,
    )


def _is_request(item: object, *, with_role: bool) -> bool:
    keys = ("rol", "flujo", "datos") if with_role else ("flujo",)
    return isinstance(item, dict) and all(key in item for key in keys)


# --- Correr un escenario -----------------------------------------------------------------


def run_scenario(scenario: Scenario, api: Api, agent: Agent, auditor: str, session: str) -> Result:
    result = Result(scenario)
    started = time.monotonic()
    try:
        prepared = [(step["rol"], _create(api, step)["numero"]) for step in scenario.prepara]
        before = {request["numero"] for request in api.get(scenario.rol, "/api/v1/solicitudes")}
        marker = _last_event(api, auditor)
        for message in scenario.mensajes:
            reply = agent.say(scenario.rol, session, message)
            result.replies.append(reply.text)
            result.cost += reply.cost
        events = _events_since(api, auditor, marker, scenario.rol)
        created = [
            request
            for request in api.get(scenario.rol, "/api/v1/solicitudes")
            if request["numero"] not in before
        ]
        states = [_find(api, role, number) for role, number in prepared]
    except AgentError as exc:
        result.failures.append(f"el agente no respondió: {exc}")
    else:
        result.failures += judge(scenario, created, states, events, result.replies)
    result.seconds = time.monotonic() - started
    return result


def _create(api: Api, step: Mapping[str, Any]) -> dict[str, Any]:
    body = {"flujo_id": step["flujo"], "datos": step["datos"]}
    created: dict[str, Any] = api.post(step["rol"], "/api/v1/solicitudes", body)
    return created


def _find(api: Api, role: str, number: str) -> dict[str, Any]:
    for request in api.get(role, "/api/v1/solicitudes"):
        if request["numero"] == number:
            found: dict[str, Any] = request
            return found
    raise EvalError(f"la solicitud preparada {number} desapareció")


def _last_event(api: Api, auditor: str) -> tuple[int, ...]:
    events = api.get(auditor, "/api/v1/auditoria?limite=1")
    return _event_key(events[0]["id"]) if events else (0, 0)


def _events_since(
    api: Api, auditor: str, marker: tuple[int, ...], role: str
) -> list[dict[str, Any]]:
    """Lo que hizo el agente de ese rol durante la conversación, según el servidor."""
    events = api.get(auditor, f"/api/v1/auditoria?limite={AUDIT_LIMIT}")
    return [
        event
        for event in events
        if _event_key(event["id"]) > marker
        and event["rol"] == role
        and event["herramienta"] != AUDIT_TOOL  # las consultas del propio juez
    ]


def _event_key(event_id: str) -> tuple[int, ...]:
    return tuple(int(part) for part in event_id.split("-"))


# --- Juzgar ------------------------------------------------------------------------------


def judge(
    scenario: Scenario,
    created: Sequence[Mapping[str, Any]],
    prepared: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    replies: Sequence[str],
) -> list[str]:
    expect = scenario.espera
    failures: list[str] = []
    if "solicitudes_permitidas" in expect:
        failures += _check_allowed(expect["solicitudes_permitidas"], created)
    else:
        failures += _check_created(expect.get("solicitudes", []), created)
    for wanted, actual in zip(expect.get("preparadas", []), prepared, strict=False):
        failures += [
            f"la solicitud {actual['numero']} quedó con {key}={actual.get(key)!r}; "
            f"se esperaba {value!r}"
            for key, value in wanted.items()
            if actual.get(key) != value
        ]
    used = {event["herramienta"] for event in events if _succeeded(event)}
    failures += [
        f"no usó la herramienta {tool}"
        for tool in expect.get("herramientas", [])
        if tool not in used
    ]
    failures += [
        f"usó una herramienta prohibida: {tool}"
        for tool in expect.get("herramientas_prohibidas", [])
        if tool in used
    ]
    text = plain("\n".join(replies))
    failures += [
        f"la respuesta no menciona «{needle}»"
        for needle in expect.get("respuesta_contiene", [])
        if plain(needle) not in text
    ]
    failures += [
        f"la respuesta menciona «{needle}»"
        for needle in expect.get("respuesta_no_contiene", [])
        if plain(needle) in text
    ]
    return failures


def _check_created(
    expected: Sequence[Mapping[str, Any]], created: Sequence[Mapping[str, Any]]
) -> list[str]:
    """Exactamente las solicitudes esperadas: ni una menos, ni una de más."""
    failures: list[str] = []
    remaining = list(created)
    for wanted in expected:
        match = next((request for request in remaining if _matches(wanted, request)), None)
        if match is None:
            failures.append(f"no se creó la solicitud esperada: {_describe(wanted)}")
        else:
            remaining.remove(match)
    failures += [f"creó una solicitud que no se esperaba: {_describe(r)}" for r in remaining]
    return failures


def _check_allowed(
    allowed: Sequence[Mapping[str, Any]], created: Sequence[Mapping[str, Any]]
) -> list[str]:
    return [
        f"creó una solicitud que no está permitida: {_describe(request)}"
        for request in created
        if not any(_matches(pattern, request) for pattern in allowed)
    ]


def _matches(wanted: Mapping[str, Any], request: Mapping[str, Any]) -> bool:
    data = request.get("datos", {})
    return (
        request.get("flujo") == wanted["flujo"]
        and wanted.get("estado", request.get("estado")) == request.get("estado")
        and all(str(data.get(key)) == str(value) for key, value in wanted.get("datos", {}).items())
    )


def _describe(request: Mapping[str, Any]) -> str:
    parts = [str(request.get("numero") or ""), request["flujo"], str(request.get("estado") or "")]
    data = request.get("datos")
    return " ".join(part for part in parts if part) + (
        f" {json.dumps(data, ensure_ascii=False)}" if data else ""
    )


def _succeeded(event: Mapping[str, Any]) -> bool:
    return not str(event["resultado"]).startswith(FAILED_OUTCOMES)


def plain(text: str) -> str:
    """Minúsculas, sin acentos y con los números sin separador de miles (637,500 = 637500)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _THOUSANDS.sub("", without_accents)


# --- Servidor y agente de verdad ---------------------------------------------------------


class HttpApi:
    def __init__(self, base_url: str, tokens: Mapping[str, str], timeout: float = 15.0) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise EvalError("La URL del servidor debe empezar con http:// o https://.")
        self._base_url = base_url.rstrip("/")
        self._tokens = tokens
        self._timeout = timeout

    def get(self, role: str, path: str) -> Any:
        return self._send(role, path, None)

    def post(self, role: str, path: str, body: Mapping[str, Any]) -> Any:
        return self._send(role, path, body)

    def _send(self, role: str, path: str, body: Mapping[str, Any] | None) -> Any:
        if role not in self._tokens:
            raise EvalError(f"Falta ROLE_TOKEN_{role.upper()} en .env.")
        request = urllib.request.Request(  # noqa: S310 - el esquema se valida en __init__
            self._base_url + path,
            data=None if body is None else json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {self._tokens[role]}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            raise EvalError(f"El servidor respondió {exc.code} en {path}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", exc)
            raise EvalError(f"No se pudo hablar con el servidor ({reason}). ¿make up?") from None


class OpenClawAgent:
    """Un turno del agente con `openclaw agent`, sin pasar por Telegram."""

    def __init__(self, command: Sequence[str], cwd: Path, timeout: int = 240) -> None:
        self._command = list(command)
        self._cwd = cwd
        self._timeout = timeout

    def say(self, role: str, session: str, message: str) -> Reply:
        command = [
            *self._command,
            "openclaw",
            "agent",
            "--agent",
            role,
            "--session-id",
            session,
            "--json",
            "--timeout",
            str(self._timeout),
            "-m",
            message,
        ]
        try:
            done = subprocess.run(  # noqa: S603 - comando fijo; el mensaje va como argumento
                command,
                cwd=self._cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self._timeout + 30,
                check=False,
            )
        except FileNotFoundError:
            raise EvalError(f"No se encontró el comando '{self._command[0]}'.") from None
        except subprocess.TimeoutExpired:
            raise AgentError(f"sin respuesta en {self._timeout} s") from None
        if done.returncode != 0:
            raise AgentError(f"openclaw terminó con código {done.returncode}")
        return parse_reply(done.stdout)


def parse_reply(output: str) -> Reply:
    start = output.find("{")
    try:
        data = json.loads(output[start:]) if start >= 0 else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or data.get("status") != "ok":
        raise AgentError("openclaw no devolvió una respuesta válida")
    result = data.get("result") or {}
    texts = [p.get("text") or "" for p in result.get("payloads") or [] if isinstance(p, dict)]
    meta = (result.get("meta") or {}).get("agentMeta") or {}
    cost = meta.get("costUsd")
    return Reply(
        "\n".join(t for t in texts if t), float(cost) if isinstance(cost, int | float) else 0.0
    )


# --- Línea de comandos -------------------------------------------------------------------


def find_auditor(api: Api, roles: Sequence[str]) -> str:
    for role in roles:
        if AUDIT_TOOL in api.get(role, "/api/v1/me")["herramientas"]:
            return role
    raise EvalError("Ningún rol puede ver la auditoría: las evals la necesitan para juzgar.")


def run_all(
    scenarios: Sequence[Scenario], api: Api, agent: Agent, roles: Sequence[str], stamp: str
) -> list[Result]:
    unknown = sorted({s.rol for s in scenarios} - set(roles))
    if unknown:
        raise EvalError(f"Escenarios con roles sin token: {', '.join(unknown)}.")
    auditor = find_auditor(api, roles)
    results = []
    for scenario in scenarios:
        result = run_scenario(scenario, api, agent, auditor, f"eval-{scenario.id}-{stamp}")
        print(format_result(result), flush=True)
        results.append(result)
    return results


def format_result(result: Result) -> str:
    mark = "OK   " if result.ok else "FALLA"
    lines = [f"{mark} {result.scenario.id:<34} {result.seconds:5.0f} s  ${result.cost:.2f}"]
    lines += [f"        - {failure}" for failure in result.failures]
    if not result.ok and result.replies:
        last = " ".join(result.replies[-1].split())
        lines.append(f"        última respuesta: {last[:300]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evals de los agentes contra el modelo real.")
    parser.add_argument("--root", type=Path, default=Path())
    parser.add_argument("--escenarios", type=Path, default=None)
    parser.add_argument("--solo", action="append", default=[], help="id de un escenario")
    parser.add_argument("--listar", action="store_true", help="solo muestra los escenarios")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--exec", dest="exec_", default=DEFAULT_EXEC, help="cómo entrar a OpenClaw")
    args = parser.parse_args(argv)
    root: Path = args.root

    try:
        scenarios = load_scenarios(args.escenarios or root / DEFAULT_SCENARIOS)
        if args.solo:
            missing = sorted(set(args.solo) - {s.id for s in scenarios})
            if missing:
                raise EvalError(f"No existe el escenario: {', '.join(missing)}.")
            scenarios = [s for s in scenarios if s.id in args.solo]
        if args.listar:
            for scenario in scenarios:
                print(f"{scenario.id:<34} [{scenario.rol}] {scenario.descripcion}")
            return 0
        env = _read_env(root)
        if (env.get("AUTH_MODE") or "demo") != "demo":
            raise EvalError("Las evals usan los agentes por rol: pon AUTH_MODE=demo y make up.")
        tokens = {
            key.removeprefix(ROLE_TOKEN_PREFIX).lower(): value
            for key, value in env.items()
            if key.startswith(ROLE_TOKEN_PREFIX) and value
        }
        base_url = args.base_url or f"http://127.0.0.1:{env.get('MCP_PORT') or '8000'}"
        api = HttpApi(base_url, tokens)
        agent = OpenClawAgent(shlex.split(args.exec_), root.resolve())
        print(f"Evals de agentes con el modelo real · {len(scenarios)} escenarios")
        results = run_all(scenarios, api, agent, list(tokens), f"{int(time.time()):x}")
    except EvalError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    passed = sum(result.ok for result in results)
    cost = sum(result.cost for result in results)
    print(f"{passed}/{len(results)} escenarios correctos · costo aproximado ${cost:.2f}")
    return 0 if passed == len(results) else 1


def _read_env(root: Path) -> dict[str, str]:
    try:
        return parse_env((root / ".env").read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvalError("No existe .env: ejecuta make tokens.") from None


if __name__ == "__main__":
    raise SystemExit(main())
