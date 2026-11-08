"""Inbox: `evento_aplicado` guarda el `id` de cada evento aplicado, en la misma transacción que la proyección."""

from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import Column, DateTime, Index, String, Table
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ia.comun.base_datos import metadata
from ia.comun.tiempo import Reloj
from ia.mensajeria.consumidor import Manejador
from ia.mensajeria.sobre import Sobre

evento_aplicado = Table(
    "evento_aplicado",
    metadata,
    Column("evento_id", UUID(as_uuid=False), primary_key=True),
    Column("tipo", String, nullable=False),
    Column("aplicado_en", DateTime(timezone=True), nullable=False),
    Index("ix_evento_aplicado_tipo_aplicado_en", "tipo", "aplicado_en"),
)


async def registrar_evento(conexion: AsyncConnection, evento_id: str, tipo: str, ahora: datetime) -> bool:
    """`True` si el evento es nuevo (se debe aplicar); `False` si ya estaba (repetido: no se hace nada)."""
    resultado = await conexion.execute(
        insert(evento_aplicado)
        .values(evento_id=evento_id, tipo=tipo, aplicado_en=ahora)
        .on_conflict_do_nothing(index_elements=["evento_id"])
        .returning(evento_aplicado.c.evento_id)
    )
    return resultado.first() is not None


AplicarEnTransaccion = Callable[[Sobre, AsyncConnection], Awaitable[None]]
"""Aplica el evento con la conexión de la transacción abierta (la misma que registra el inbox)."""


def manejador_con_inbox(motor: AsyncEngine, aplicar: AplicarEnTransaccion, reloj: Reloj) -> Manejador:
    """Manejador que, en **una transacción**, registra el `id` en `evento_aplicado` y aplica el evento.

    Si el `id` ya estaba, no hace nada (`False`). Si `aplicar` falla, se deshace todo (también el inbox), así el
    reintento lo vuelve a aplicar desde cero.
    """

    async def manejar(sobre: Sobre) -> bool:
        async with motor.begin() as conexion:
            if not await registrar_evento(conexion, sobre.id, sobre.tipo, reloj.ahora()):
                return False
            await aplicar(sobre, conexion)
        return True

    return manejar
