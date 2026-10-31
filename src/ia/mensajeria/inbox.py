"""Inbox: `evento_aplicado` guarda el `id` de cada evento aplicado, en la misma transacción que la proyección."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, String, Table
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ia.comun.base_datos import metadata

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
