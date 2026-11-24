"""Solicitudes: se crean con el motor de flujos, se guardan en ServiceNow y se aprueban aquí.

Las reglas de quién decide viven en el servidor:
- solo el rol indicado en `aprueba` del flujo puede aprobar o rechazar;
- nadie decide sobre sus propias solicitudes;
- una solicitud ya decidida no se vuelve a decidir.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import date
from enum import StrEnum

from pydantic import BaseModel

from assistant.auth import Caller
from assistant.company import Company, Flow
from assistant.company.models import Calculated, Calculation
from assistant.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from assistant.flows import (
    FieldValue,
    FlowContext,
    Outcome,
    decide,
    service_year,
    validate_data,
)
from assistant.flows.calculations import as_date
from assistant.servicenow_client import Record, ServiceNowClient

TABLE = "sc_request"
MAX_COMMENT_CHARS = 500
MAX_VACATION_REQUESTS = 500
# Los valores que se insertan en sysparm_query se validan: así nadie puede inyectar "^campo=...".
_REQUEST_NUMBER = re.compile(r"^REQ\d{7}$")


class RequestStatus(StrEnum):
    PENDIENTE_APROBACION = "pendiente_aprobacion"
    APROBADA = "aprobada"
    APROBADA_AUTOMATICAMENTE = "aprobada_automaticamente"
    RECHAZADA = "rechazada"


_FROM_OUTCOME = {
    Outcome.PENDIENTE_APROBACION: RequestStatus.PENDIENTE_APROBACION,
    Outcome.APROBADA_AUTOMATICAMENTE: RequestStatus.APROBADA_AUTOMATICAMENTE,
    Outcome.RECHAZADA: RequestStatus.RECHAZADA,
}
# Campos estándar de ServiceNow para que la solicitud se vea bien también en su interfaz.
_SERVICENOW_STATE = {
    RequestStatus.PENDIENTE_APROBACION: ("requested", "requested"),
    RequestStatus.APROBADA: ("approved", "closed_complete"),
    RequestStatus.APROBADA_AUTOMATICAMENTE: ("not requested", "closed_complete"),
    RequestStatus.RECHAZADA: ("rejected", "closed_rejected"),
}


class RequestSummary(BaseModel):
    numero: str
    flujo: str
    flujo_nombre: str
    estado: RequestStatus
    motivo: str
    solicitante: str
    rol_solicitante: str
    datos: dict[str, str]
    creada: str
    aprueba: str | None = None
    decidida_por: str | None = None
    comentario: str | None = None


class RequestsService:
    def __init__(self, company: Company, servicenow: ServiceNowClient) -> None:
        self._flows = {flow.id: flow for flow in company.flujos}
        self._employees = {employee.id: employee for employee in company.empleados}
        self._names = {employee.id: employee.nombre for employee in company.empleados}
        self._context = FlowContext.from_company(company)
        self._servicenow = servicenow

    async def start(
        self, caller: Caller, flow_id: str, raw: Mapping[str, object]
    ) -> RequestSummary:
        flow = self._flow_for(caller, flow_id)
        datos = validate_data(flow, raw, self._context)
        decision = decide(flow, datos, await self._context_for(caller, flow, datos))
        status = _FROM_OUTCOME[decision.estado]
        approval, state = _SERVICENOW_STATE[status]
        record = await self._servicenow.create(
            TABLE,
            {
                "short_description": f"{flow.nombre} - {caller.name}",
                "description": decision.motivo,
                "approval": approval,
                "request_state": state,
                "u_flujo": flow.id,
                "u_datos": json.dumps(
                    {k: _as_text(v) for k, v in decision.datos.items()}, ensure_ascii=False
                ),
                "u_estado": status,
                "u_motivo": decision.motivo,
                "u_aprueba": decision.aprueba or "",
                "u_solicitante_id": caller.employee_id,
                "u_rol_solicitante": caller.role,
            },
        )
        return self._summary(record)

    async def mine(self, caller: Caller, status: str | None = None) -> list[RequestSummary]:
        query = f"u_solicitante_id={caller.employee_id}"
        if status:
            query += f"^u_estado={_parse_status(status)}"
        records = await self._servicenow.query(TABLE, f"{query}^ORDERBYDESCnumber")
        return [self._summary(record) for record in records]

    async def pending_for(self, caller: Caller) -> list[RequestSummary]:
        """Pendientes que le toca decidir a su rol; las propias no aparecen."""
        query = (
            f"u_estado={RequestStatus.PENDIENTE_APROBACION}^u_aprueba={caller.role}"
            f"^u_solicitante_id!={caller.employee_id}^ORDERBYnumber"
        )
        return [self._summary(record) for record in await self._servicenow.query(TABLE, query)]

    async def decide(
        self, caller: Caller, number: str, *, approve: bool, comment: str | None = None
    ) -> RequestSummary:
        comment_text = _clean_comment(comment)
        record = await self._find(number)
        if record.get("u_aprueba") != caller.role:
            raise ForbiddenError(f"La solicitud {number} no le corresponde decidirla a tu rol.")
        if record.get("u_solicitante_id") == caller.employee_id:
            raise ForbiddenError("No puedes aprobar ni rechazar tus propias solicitudes.")
        if record.get("u_estado") != RequestStatus.PENDIENTE_APROBACION:
            raise ConflictError(
                f"La solicitud {number} ya fue decidida ({record.get('u_estado')})."
            )

        status = RequestStatus.APROBADA if approve else RequestStatus.RECHAZADA
        approval, state = _SERVICENOW_STATE[status]
        updated = await self._servicenow.update(
            TABLE,
            record["sys_id"],
            {
                "approval": approval,
                "request_state": state,
                "u_estado": status,
                "u_decidida_por": caller.employee_id,
                "u_comentario": comment_text,
            },
        )
        return self._summary(updated)

    async def _context_for(
        self, caller: Caller, flow: Flow, datos: Mapping[str, FieldValue]
    ) -> FlowContext:
        """Antigüedad del solicitante y, si el flujo lo usa, sus vacaciones ya pedidas."""
        hired = self._employees[caller.employee_id].fecha_ingreso
        used = 0
        balance = flow.calculation(Calculation.SALDO_VACACIONES)
        if balance is not None:
            start = as_date(datos[balance.con["inicio"]])
            used = await self._vacation_days_used(caller, flow, balance, start)
        return replace(self._context, fecha_ingreso=hired, dias_vacaciones_usados=used)

    async def _vacation_days_used(
        self, caller: Caller, flow: Flow, balance: Calculated, start: date
    ) -> int:
        """Días de solicitudes vigentes (pendientes o aprobadas) en el mismo año de servicio."""
        hired = self._employees[caller.employee_id].fecha_ingreso
        year_start, year_end = service_year(hired, start)
        query = f"u_solicitante_id={caller.employee_id}^u_flujo={flow.id}"
        used = 0
        for record in await self._servicenow.query(TABLE, query, limit=MAX_VACATION_REQUESTS):
            if record.get("u_estado") == RequestStatus.RECHAZADA:
                continue
            datos = json.loads(record.get("u_datos") or "{}")
            try:
                requested_start = date.fromisoformat(datos[balance.con["inicio"]])
                days = int(datos[balance.con["dias"]])
            except (KeyError, ValueError):
                continue  # solicitud vieja con otro formato: no cuenta
            if year_start <= requested_start < year_end:
                used += days
        return used

    def _flow_for(self, caller: Caller, flow_id: str) -> Flow:
        flow = self._flows.get(flow_id)
        if flow is None or caller.role not in flow.roles:
            raise NotFoundError(f"No existe el flujo '{flow_id}' para tu rol.")
        return flow

    async def _find(self, number: str) -> Record:
        if not _REQUEST_NUMBER.match(number):
            raise InvalidInputError("El número de solicitud debe tener el formato REQ0010001.")
        found = await self._servicenow.query(TABLE, f"number={number}")
        if not found:
            raise NotFoundError(f"No existe la solicitud {number}.")
        return found[0]

    def _summary(self, record: Record) -> RequestSummary:
        flow = self._flows.get(record.get("u_flujo", ""))
        decided_by = record.get("u_decidida_por") or None
        return RequestSummary(
            numero=record["number"],
            flujo=record.get("u_flujo", ""),
            flujo_nombre=flow.nombre if flow else "",
            estado=RequestStatus(record["u_estado"]),
            motivo=record.get("u_motivo", ""),
            solicitante=self._names.get(record.get("u_solicitante_id", ""), ""),
            rol_solicitante=record.get("u_rol_solicitante", ""),
            datos=json.loads(record.get("u_datos") or "{}"),
            creada=record.get("sys_created_on", ""),
            aprueba=record.get("u_aprueba") or None,
            decidida_por=self._names.get(decided_by, decided_by) if decided_by else None,
            comentario=record.get("u_comentario") or None,
        )


def _parse_status(status: str) -> RequestStatus:
    try:
        return RequestStatus(status)
    except ValueError:
        options = ", ".join(s.value for s in RequestStatus)
        raise InvalidInputError(f"Estado inválido. Usa uno de: {options}.") from None


def _clean_comment(comment: str | None) -> str:
    text = (comment or "").strip()
    if len(text) > MAX_COMMENT_CHARS:
        raise InvalidInputError(
            f"El comentario debe tener como máximo {MAX_COMMENT_CHARS} caracteres."
        )
    return text


def _as_text(value: FieldValue) -> str:
    return value.isoformat() if isinstance(value, date) else str(value)
