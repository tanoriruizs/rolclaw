"""Consulta de flujos filtrada por rol."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from assistant.company import Flow, Rule, RuleAction
from assistant.company.models import RequiredField
from assistant.errors import NotFoundError


class FlowSummary(BaseModel):
    id: str
    nombre: str
    descripcion: str


class FlowDetail(FlowSummary):
    datos_requeridos: tuple[RequiredField, ...]
    pasos: tuple[str, ...]
    reglas: list[str]


class FlowCatalog:
    def __init__(self, flows: Iterable[Flow]) -> None:
        self._flows = {flow.id: flow for flow in flows}

    def list_for(self, role: str) -> list[FlowSummary]:
        return [
            FlowSummary(id=flow.id, nombre=flow.nombre, descripcion=flow.descripcion)
            for flow in self._flows.values()
            if role in flow.roles
        ]

    def get_for(self, role: str, flow_id: str) -> FlowDetail:
        flow = self._flows.get(flow_id)
        if flow is None or role not in flow.roles:
            # Mismo mensaje si no existe o si es de otro rol: no revelamos que existe.
            raise NotFoundError(f"No existe el flujo '{flow_id}' para tu rol.")
        return FlowDetail(
            id=flow.id,
            nombre=flow.nombre,
            descripcion=flow.descripcion,
            datos_requeridos=flow.datos_requeridos,
            pasos=flow.pasos,
            reglas=[describe_rule(rule) for rule in flow.reglas],
        )


def describe_rule(rule: Rule) -> str:
    if rule.mayor_que is not None:
        condition = f"{rule.campo} es mayor que {rule.mayor_que}"
    else:
        condition = f"{rule.campo} es menor que {rule.menor_que}"
    if rule.accion is RuleAction.RECHAZAR:
        return f"Si {condition}, se rechaza: {rule.mensaje}"
    return f"Si {condition}, requiere aprobación de {rule.aprueba}."
