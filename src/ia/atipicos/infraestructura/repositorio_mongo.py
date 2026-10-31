"""`ia_db.consumo_atipico` (403, diseño «`ia_db.consumo_atipico`»)."""

from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from ia.atipicos.dominio.valores import CONFIRMADO, NORMAL

COLECCION = "consumo_atipico"


async def contar_revisiones(base: AsyncDatabase[dict[str, Any]]) -> dict[str, int]:
    """Consumos atípicos revisados por estado (`NORMAL`, `CONFIRMADO`); lo usa el reentrenamiento como referencia."""
    conteo = {NORMAL: 0, CONFIRMADO: 0}
    cursor = await base[COLECCION].aggregate(
        [
            {"$match": {"estado_revision": {"$in": [NORMAL, CONFIRMADO]}}},
            {"$group": {"_id": "$estado_revision", "n": {"$sum": 1}}},
        ]
    )
    async for fila in cursor:
        conteo[str(fila["_id"])] = int(fila["n"])
    return conteo
