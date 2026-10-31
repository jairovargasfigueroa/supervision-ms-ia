"""Salidas de `rpt_movimiento` (401) para los consumos atípicos: solo lectura de la base de reportes."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.atipicos.dominio.variables import SalidaMaterial
from ia.reportes.dominio.valores import SALIDA
from ia.reportes.infraestructura.tablas import rpt_movimiento


async def leer_salidas(motor: AsyncEngine) -> list[SalidaMaterial]:
    """Todas las salidas (las de los eventos y las de la carga inicial), en orden de fecha (para reentrenar)."""
    m = rpt_movimiento.c
    consulta = (
        select(m.movimiento_id, m.obra_id, m.material_id, m.cantidad, m.periodo, m.supera_stock, m.registrado_en)
        .where(m.tipo == SALIDA)
        .order_by(m.registrado_en, m.movimiento_id)
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
