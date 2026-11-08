"""Visibilidad de los agentes (502): residente con `rpt_obra_resumen.residente_id` (401) y sin Control de cobros."""

from typing import Any

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo import reportes_503 as d
from ia.agentes.aplicacion.visibilidad import alcance_de
from ia.agentes.dominio.ejecucion import Sugerencia
from ia.atipicos.infraestructura.obras_sql import ObrasReportesSql
from ia.auth.usuario import Usuario
from ia.reportes.infraestructura.tablas import rpt_obra_resumen

pytestmark = pytest.mark.integracion

OTRO_RESIDENTE = "a0000000-0000-4000-8000-000000000009"


def _sugerencia(agente: str, obra: str) -> Sugerencia:
    return Sugerencia("s", "e", agente, obra, "x", "PROGRAMAR_VISITA", "d", 0, d.AHORA)


async def _obras(motor: AsyncEngine) -> None:
    filas: list[dict[str, Any]] = [dict(o) for o in d.OBRAS]
    filas[1]["residente_id"] = OTRO_RESIDENTE
    async with motor.begin() as conexion:
        for fila in filas:
            await conexion.execute(insert(rpt_obra_resumen), [fila])


async def test_admin_y_gerente_ven_todo(motor: AsyncEngine) -> None:
    for rol in ("admin", "gerente"):
        alcance = await alcance_de(Usuario("u", frozenset({rol})), ObrasReportesSql(motor))
        assert alcance.agentes == ("SEGUIMIENTO_OBRAS", "CONTROL_MATERIALES", "CONTROL_COBROS")
        assert alcance.obra_ids is None
        assert alcance.ve_sugerencia(_sugerencia("CONTROL_COBROS", d.O2))


async def test_residente_solo_sus_obras_y_nunca_cobros(motor: AsyncEngine) -> None:
    await _obras(motor)
    alcance = await alcance_de(Usuario(d.R1, frozenset({"residente"})), ObrasReportesSql(motor))
    assert alcance.agentes == ("SEGUIMIENTO_OBRAS", "CONTROL_MATERIALES")
    assert alcance.obra_ids == frozenset({d.O1, d.O3})
    assert alcance.ve_sugerencia(_sugerencia("SEGUIMIENTO_OBRAS", d.O1))
    assert not alcance.ve_sugerencia(_sugerencia("SEGUIMIENTO_OBRAS", d.O2))  # obra ajena
    assert not alcance.ve_sugerencia(_sugerencia("CONTROL_COBROS", d.O1))  # nunca cobros
    sin_obras = await alcance_de(Usuario(OTRO_RESIDENTE + "0", frozenset({"residente"})), ObrasReportesSql(motor))
    assert sin_obras.obra_ids == frozenset()
