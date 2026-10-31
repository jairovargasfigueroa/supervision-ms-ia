"""`ia_db.consumo_atipico` (403, diseño «`ia_db.consumo_atipico`»)."""

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from bson.decimal128 import Decimal128
from pymongo.asynchronous.database import AsyncDatabase

from ia.atipicos.dominio.consumo import ConsumoAtipico
from ia.atipicos.dominio.valores import CONFIRMADO, NORMAL
from ia.comun.mongo import IndiceMongo, registrar_indice

COLECCION = "consumo_atipico"

registrar_indice(
    IndiceMongo(
        coleccion=COLECCION,
        claves=(("obra_id", 1), ("estado_revision", 1), ("registrado_en", -1)),
        nombre="ix_consumo_atipico_obra_estado_registrado",
    )
)

Documento = dict[str, Any]


class RepositorioAtipicosMongo:
    def __init__(self, base: AsyncDatabase[Documento]) -> None:
        self._coleccion = base[COLECCION]

    async def guardar_si_no_existe(self, consumo: ConsumoAtipico) -> bool:
        documento = a_documento(consumo)
        resultado = await self._coleccion.update_one(
            {"_id": documento.pop("_id")}, {"$setOnInsert": documento}, upsert=True
        )
        return resultado.upserted_id is not None

    async def obtener(self, consumo_id: str) -> ConsumoAtipico | None:
        documento = await self._coleccion.find_one({"_id": consumo_id})
        return None if documento is None else de_documento(documento)


def a_documento(consumo: ConsumoAtipico) -> Documento:
    return {
        "_id": consumo.id,
        "obra_id": consumo.obra_id,
        "material_id": consumo.material_id,
        "partida_id": consumo.partida_id,
        "cantidad": Decimal128(str(consumo.cantidad)),
        "periodo": consumo.periodo,
        "registrado_en": consumo.registrado_en,
        "puntaje": float(consumo.puntaje),
        "motivos": list(consumo.motivos),
        "variables": dict(consumo.variables),
        "version_modelo": consumo.version_modelo,
        "detectado_en": consumo.detectado_en,
        "estado_revision": consumo.estado_revision,
        "revisado_por": consumo.revisado_por,
        "revisado_en": consumo.revisado_en,
        "comentario": consumo.comentario,
    }


def de_documento(documento: Mapping[str, Any]) -> ConsumoAtipico:
    cantidad = documento["cantidad"]
    return ConsumoAtipico(
        id=str(documento["_id"]),
        obra_id=str(documento["obra_id"]),
        material_id=str(documento["material_id"]),
        partida_id=str(documento["partida_id"]),
        cantidad=cantidad.to_decimal() if isinstance(cantidad, Decimal128) else Decimal(str(cantidad)),
        periodo=str(documento["periodo"]),
        registrado_en=documento["registrado_en"],
        puntaje=float(documento["puntaje"]),
        motivos=tuple(documento.get("motivos") or ()),
        variables=dict(documento.get("variables") or {}),
        version_modelo=str(documento["version_modelo"]),
        detectado_en=documento["detectado_en"],
        estado_revision=str(documento["estado_revision"]),
        revisado_por=documento.get("revisado_por"),
        revisado_en=documento.get("revisado_en"),
        comentario=documento.get("comentario"),
    )


async def contar_revisiones(base: AsyncDatabase[Documento]) -> dict[str, int]:
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
