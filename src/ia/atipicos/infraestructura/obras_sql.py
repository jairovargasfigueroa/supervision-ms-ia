"""Obras de `rpt_obra_resumen` (401) para la visibilidad de los consumos atípicos (solo lectura)."""

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.atipicos.aplicacion.puertos import ObraConocida
from ia.reportes.infraestructura.tablas import rpt_obra_resumen

_o = rpt_obra_resumen.c


class ObrasReportesSql:
    def __init__(self, motor: AsyncEngine) -> None:
        self._motor = motor

    async def obras(self, obra_ids: Sequence[str]) -> dict[str, ObraConocida]:
        validos = [obra_id for obra_id in obra_ids if _es_uuid(obra_id)]
        if not validos:
            return {}
        async with self._motor.connect() as conexion:
            filas = (await conexion.execute(select(_o.obra_id, _o.residente_id).where(_o.obra_id.in_(validos)))).all()
        return {
            str(f.obra_id): ObraConocida(str(f.obra_id), None if f.residente_id is None else str(f.residente_id))
            for f in filas
        }

    async def obras_del_residente(self, residente_id: str) -> list[str]:
        if not _es_uuid(residente_id):
            return []
        async with self._motor.connect() as conexion:
            filas = (await conexion.execute(select(_o.obra_id).where(_o.residente_id == residente_id))).all()
        return [str(f.obra_id) for f in filas]


def _es_uuid(texto: str) -> bool:
    try:
        uuid.UUID(texto)
    except ValueError:
        return False
    return True
