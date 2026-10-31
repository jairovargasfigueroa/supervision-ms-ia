"""Lo que el riesgo lee y escribe en la base de reportes (401) y lo que cuenta de `consumo_atipico` (403)."""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.comun.tiempo import ZONA_NEGOCIO
from ia.reportes.dominio.valores import APROBADA, EN_EJECUCION, SALIDA
from ia.reportes.infraestructura.tablas import rpt_movimiento, rpt_obra_resumen, rpt_planilla
from ia.riesgo.aplicacion.puertos import ObraParaRiesgo
from ia.riesgo.dominio.variables import EstadoObra, PlanillaAprobadaObra

COLECCION_ATIPICOS = "consumo_atipico"
DIAS_SIN_STOCK = 90


class FuenteRiesgoSql:
    def __init__(self, motor: AsyncEngine, zona: str = ZONA_NEGOCIO) -> None:
        self._motor = motor
        self._zona = zona

    async def leer_obra(self, obra_id: str, hoy: date) -> ObraParaRiesgo | None:
        o = rpt_obra_resumen.c
        p = rpt_planilla.c
        m = rpt_movimiento.c
        async with self._motor.connect() as conexion:
            obra = (
                await conexion.execute(
                    select(o.estado, o.fecha_inicio, o.plazo_dias, o.monto_contrato, o.avance_pct, o.plazo_pct).where(
                        o.obra_id == obra_id
                    )
                )
            ).first()
            if obra is None:
                return None
            aprobadas = (
                await conexion.execute(
                    select(p.periodo, p.avance_periodo_pct).where(
                        p.obra_id == obra_id, p.estado == APROBADA, p.periodo.is_not(None)
                    )
                )
            ).all()
            observadas = (
                await conexion.execute(
                    select(func.coalesce(func.sum(p.veces_observada), 0)).where(p.obra_id == obra_id)
                )
            ).scalar_one()
            desde = _inicio_del_dia(hoy - timedelta(days=DIAS_SIN_STOCK), self._zona)
            sin_stock = (
                await conexion.execute(
                    select(func.count()).where(
                        m.obra_id == obra_id, m.tipo == SALIDA, m.supera_stock.is_(True), m.registrado_en >= desde
                    )
                )
            ).scalar_one()
        estado_obra = None
        if obra.fecha_inicio is not None and obra.plazo_dias and obra.monto_contrato:
            estado_obra = EstadoObra(
                fecha_inicio=obra.fecha_inicio,
                plazo_dias=int(obra.plazo_dias),
                monto_contrato=float(obra.monto_contrato),
                avance_pct=float(obra.avance_pct),
                plazo_pct=float(obra.plazo_pct),
                planillas_aprobadas=tuple(
                    PlanillaAprobadaObra(str(f.periodo), float(f.avance_periodo_pct or 0)) for f in aprobadas
                ),
                planillas_observadas=int(observadas),
                atipicos_90d=0,  # lo completa el servicio con ContadorAtipicos
                supera_stock_90d=int(sin_stock),
            )
        return ObraParaRiesgo(obra_id, obra.estado, float(obra.plazo_pct), estado_obra)

    async def obras(self, solo_en_ejecucion: bool) -> list[str]:
        o = rpt_obra_resumen.c
        consulta = select(o.obra_id).order_by(o.obra_id)
        if solo_en_ejecucion:
            consulta = consulta.where(o.estado == EN_EJECUCION)
        async with self._motor.connect() as conexion:
            return [str(fila.obra_id) for fila in await conexion.execute(consulta)]

    async def guardar_riesgo(self, obra_id: str, riesgo: float | None, nivel: str, ahora: datetime) -> None:
        async with self._motor.begin() as conexion:
            await conexion.execute(
                update(rpt_obra_resumen)
                .where(rpt_obra_resumen.c.obra_id == obra_id)
                .values(
                    riesgo_retraso=None if riesgo is None else round(riesgo, 3),
                    nivel_riesgo=nivel,
                    actualizado_en=ahora,
                )
            )


class ContadorAtipicosMongo:
    """`ia_db.consumo_atipico` (lo llena la 403): mientras no exista, cuenta 0."""

    def __init__(self, base: AsyncDatabase[dict[str, Any]]) -> None:
        self._coleccion = base[COLECCION_ATIPICOS]

    async def contar(self, obra_id: str, desde: datetime) -> int:
        return await self._coleccion.count_documents(
            {"obra_id": obra_id, "detectado_en": {"$gte": desde}, "estado_revision": {"$ne": "NORMAL"}}
        )


def _inicio_del_dia(dia: date, zona: str) -> datetime:
    return datetime.combine(dia, datetime.min.time(), ZoneInfo(zona))
