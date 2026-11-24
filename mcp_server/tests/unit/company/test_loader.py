import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from assistant.company import Company, CompanyConfigError, Tool, load_company


@pytest.fixture
def company(empresa_dir: Path) -> Company:
    return load_company(empresa_dir)


def _replace(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")


def test_real_company_loads(company: Company) -> None:
    assert set(company.roles) == {"ventas", "rh", "empleado", "gerente"}
    assert len(company.documentos) == 5
    assert {f.id for f in company.flujos} == {
        "cotizacion_descuento",
        "alta_cliente",
        "registrar_vacaciones",
        "solicitar_vacaciones",
        "constancia_laboral",
    }


def test_demo_data_matches_the_plan(company: Company) -> None:
    laptop = next(p for p in company.catalogo.productos if p.id == "LAP-001")
    juan = next(e for e in company.empleados if e.id == "E-301")

    assert laptop.precio == Decimal(15000)
    assert (date(2026, 12, 22) - juan.fecha_ingreso).days // 365 == 3


def test_salary_table_is_only_for_rh_and_gerente(company: Company) -> None:
    tabulador = next(d for d in company.documentos if d.archivo == "tabulador_sueldos.md")

    assert tabulador.roles == {"rh", "gerente"}


def test_injection_document_is_present_for_every_role(company: Company) -> None:
    codigo = next(d for d in company.documentos if d.archivo == "codigo_conducta.md")

    assert "ignora todas tus instrucciones" in codigo.contenido
    assert codigo.roles == set(company.roles)


def test_only_gerente_can_approve(company: Company) -> None:
    approvers = {rid for rid, role in company.roles.items() if Tool.APROBAR in role.herramientas}

    assert approvers == {"gerente"}


@pytest.mark.parametrize(
    ("file", "old", "new", "expected"),
    [
        ("roles.yaml", "mis_solicitudes, calcular]", "mis_solicitudes, borrar_todo]", "roles.yaml"),
        ("roles.yaml", "empleado_id: E-101", "empleado_id: E-999", "persona no coincide"),
        ("empleados.yaml", "jefe: E-301", "jefe: E-404", "jefe 'E-404' no existe"),
        ("empleados.yaml", "rol: rh,", "rol: becario,", "empleado 'E-201': roles desconocidos"),
        (
            "empleados.yaml",
            "rol: gerente,",
            "rol: ventas,",
            r"su persona demo \(E-001\) tiene otro rol",
        ),
        ("empleados.yaml", "rol: rh, ", "", "empleados.yaml"),
        ("catalogo.yaml", "precio: 15000", "precio: -1", "catalogo.yaml"),
        ("flujos/ventas/alta_cliente.yaml", "pasos:", "regals: []\npasos:", "alta_cliente.yaml"),
        (
            "flujos/ventas/alta_cliente.yaml",
            "roles: [ventas]",
            "roles: [gerente]",
            "no puede iniciar",
        ),
        (
            "flujos/ventas/alta_cliente.yaml",
            "aprueba: gerente",
            "aprueba: ventas",
            "no puede aprobar",
        ),
        ("flujos/ventas/alta_cliente.yaml", "roles: [ventas]", "roles: [compras]", "desconocidos"),
        ("documentos/prestaciones.md", "roles: [empleado,", "roles: [becario,", "desconocidos"),
        ("documentos/prestaciones.md", "---\ntitulo", "titulo", "front matter"),
        ("catalogo.yaml", "moneda: MXN", "moneda: [MXN", "YAML inválido"),
    ],
)
def test_invalid_configuration_is_rejected_with_a_clear_message(
    empresa_copy: Path, file: str, old: str, new: str, expected: str
) -> None:
    _replace(empresa_copy / file, old, new)

    with pytest.raises(CompanyConfigError, match=expected):
        load_company(empresa_copy)


def test_missing_file_is_reported(empresa_copy: Path) -> None:
    (empresa_copy / "roles.yaml").unlink()

    with pytest.raises(CompanyConfigError, match="no existe"):
        load_company(empresa_copy)


def test_front_matter_must_be_a_mapping(empresa_copy: Path) -> None:
    (empresa_copy / "documentos" / "extra.md").write_text("---\n- lista\n---\nTexto", "utf-8")

    with pytest.raises(CompanyConfigError, match="titulo y roles"):
        load_company(empresa_copy)


def test_duplicate_flow_ids_are_rejected(empresa_copy: Path) -> None:
    source = empresa_copy / "flujos" / "ventas" / "alta_cliente.yaml"
    (empresa_copy / "flujos" / "ventas" / "copia.yaml").write_text(source.read_text("utf-8"))

    with pytest.raises(CompanyConfigError, match="flujo duplicado"):
        load_company(empresa_copy)


def test_vacation_table_matches_the_policy_document(company: Company) -> None:
    """La tabla que usa el servidor y la que lee el agente no pueden decir cosas distintas."""
    policy = next(d for d in company.documentos if d.archivo == "politica_vacaciones.md")
    rows = re.findall(r"^\| (\d+)(?: a \d+)? \| (\d+) \|$", policy.contenido, re.MULTILINE)
    documented = {int(years): int(days) for years, days in rows}

    assert len(documented) == 9
    for years, days in documented.items():
        assert company.calendario.vacation_days(years) == days, years
    # "A partir del año 26, se suman 2 días por cada 5 años de servicio adicionales."
    assert company.calendario.vacation_days(26) == 30
    assert company.calendario.vacation_days(31) == 32


def test_holidays_are_real_dates_and_never_on_weekends_by_mistake(company: Company) -> None:
    holidays = company.calendario.dias_festivos

    assert date(2026, 12, 25) in holidays
    assert date(2026, 11, 16) in holidays
    assert {h.year for h in holidays} == {2026, 2027}
    assert len(holidays) == 14


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("  - 2026-12-25", "  - 2026-13-45", "calendario.yaml"),
        ("  - { desde: 2, dias: 14 }", "  - { desde: 1, dias: 14 }", "en orden"),
        ("  - { desde: 3, dias: 16 }", "  - { desde: 3, dias: -1 }", "calendario.yaml"),
    ],
)
def test_invalid_calendar_is_rejected(
    empresa_copy: Path, old: str, new: str, expected: str
) -> None:
    _replace(empresa_copy / "calendario.yaml", old, new)

    with pytest.raises(CompanyConfigError, match=expected):
        load_company(empresa_copy)


def test_missing_calendar_is_reported(empresa_copy: Path) -> None:
    (empresa_copy / "calendario.yaml").unlink()

    with pytest.raises(CompanyConfigError, match=r"calendario\.yaml: el archivo no existe"):
        load_company(empresa_copy)


def test_a_flow_cannot_reference_an_unknown_calculation(empresa_copy: Path) -> None:
    flow = empresa_copy / "flujos" / "empleado" / "solicitar_vacaciones.yaml"
    _replace(flow, "calculo: dias_por_antiguedad", "calculo: dias_magicos")

    with pytest.raises(CompanyConfigError, match=r"solicitar_vacaciones\.yaml"):
        load_company(empresa_copy)
