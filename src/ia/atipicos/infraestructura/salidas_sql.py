"""Salidas de `rpt_movimiento` (401) para los consumos atípicos: solo lectura de la base de reportes."""

from collections.abc import Mapping

from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ia.atipicos.dominio.variables import (
    VENTANA_MATERIAL,
    VENTANA_OBRA_MATERIAL,
    Historial,
    SalidaMaterial,
    SalidaPrevia,
)
from ia.mensajeria.inbox import evento_aplicado
from ia.reportes.dominio.valores import SALIDA
from ia.reportes.infraestructura.tablas import rpt_movimiento

_m = rpt_movimiento.c


class HistorialSalidasSql:
    """Arma el mismo `Historial` que `HistorialEnMemoria` con tres consultas (usan los índices de la 401)."""

    def __init__(self, motor: AsyncEngine) -> None:
        self._motor = motor

    async def historial_de(self, salida: SalidaMaterial) -> Historial:
        anteriores = and_(
            _m.tipo == SALIDA,
            _m.registrado_en < salida.registrado_en,
            _m.movimiento_id != salida.movimiento_id,
        )
        de_la_obra = and_(anteriores, _m.obra_id == salida.obra_id, _m.material_id == salida.material_id)
        async with self._motor.connect() as conexion:
            recientes = (
                await conexion.execute(
                    select(_m.cantidad, _m.registrado_en)
                    .where(de_la_obra)
                    .order_by(_m.registrado_en.desc(), _m.movimiento_id.desc())
                    .limit(VENTANA_OBRA_MATERIAL)
                )
            ).all()
            del_material = (
                await conexion.execute(
                    select(_m.cantidad)
                    .where(anteriores, _m.material_id == salida.material_id)
                    .order_by(_m.registrado_en.desc(), _m.movimiento_id.desc())
                    .limit(VENTANA_MATERIAL)
                )
            ).all()
            periodos = await _consumo_por_periodo(conexion, de_la_obra)
        return Historial(
            obra_material=[SalidaPrevia(float(f.cantidad), f.registrado_en) for f in reversed(recientes)],
            material=[float(f.cantidad) for f in reversed(del_material)],
            consumo_por_periodo=periodos,
        )


async def _consumo_por_periodo(conexion: AsyncConnection, filtro: ColumnElement[bool]) -> Mapping[str, float]:
    filas = (
        await conexion.execute(
            select(_m.periodo, func.sum(_m.cantidad).label("cantidad")).where(filtro).group_by(_m.periodo)
        )
    ).all()
    return {str(f.periodo): float(f.cantidad) for f in filas}


class EventosAplicadosSql:
    def __init__(self, motor: AsyncEngine) -> None:
        self._motor = motor

    async def ya_aplicado(self, evento_id: str) -> bool:
        async with self._motor.connect() as conexion:
            fila = (
                await conexion.execute(
                    select(evento_aplicado.c.evento_id).where(evento_aplicado.c.evento_id == evento_id)
                )
            ).first()
        return fila is not None


async def leer_salidas(motor: AsyncEngine) -> list[SalidaMaterial]:
    """Todas las salidas (las de los eventos y las de la carga inicial), en orden de fecha (para reentrenar)."""
    consulta = (
        select(_m.movimiento_id, _m.obra_id, _m.material_id, _m.cantidad, _m.periodo, _m.supera_stock, _m.registrado_en)
        .where(_m.tipo == SALIDA)
        .order_by(_m.registrado_en, _m.movimiento_id)
    )
    async with motor.connect() as conexion:
        filas = (await conexion.execute(consulta)).all()
    return [
        SalidaMaterial(
            movimiento_id=str(fila.movimiento_id),
            obra_id=str(fila.obra_id),
            material_id=fila.material_id,
            cantidad=float(fila.cantidad),
            periodo=str(fila.periodo),
            supera_stock=bool(fila.supera_stock),
            registrado_en=fila.registrado_en,
        )
        for fila in filas
    ]
