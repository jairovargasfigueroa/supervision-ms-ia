"""`ia_db.consumo_atipico` (403, diseño «`ia_db.consumo_atipico`»)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from bson.decimal128 import Decimal128
from pymongo import DESCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase

from ia.atipicos.dominio.consumo import ConsumoAtipico
from ia.atipicos.dominio.valores import CONFIRMADO, NORMAL, PENDIENTE
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

    async def listar(
        self,
        obra_ids: Sequence[str] | None,
        estado: str | None,
        desde: datetime | None,
        hasta: datetime | None,
        limite: int,
    ) -> list[ConsumoAtipico]:
        filtro: Documento = {}
        if obra_ids is not None:
            filtro["obra_id"] = {"$in": list(obra_ids)}
        if estado is not None:
            filtro["estado_revision"] = estado
        rango: Documento = {}
        if desde is not None:
            rango["$gte"] = desde
        if hasta is not None:
            rango["$lt"] = hasta
        if rango:
            filtro["registrado_en"] = rango
        cursor = self._coleccion.find(filtro).sort([("registrado_en", DESCENDING), ("_id", DESCENDING)]).limit(limite)
        return [de_documento(documento) async for documento in cursor]

    async def pendientes_por_obra(self, obra_ids: Sequence[str]) -> dict[str, int]:
        cursor = await self._coleccion.aggregate(
            [
                {"$match": {"obra_id": {"$in": list(obra_ids)}, "estado_revision": PENDIENTE}},
                {"$group": {"_id": "$obra_id", "pendientes": {"$sum": 1}}},
            ]
        )
        return {str(fila["_id"]): int(fila["pendientes"]) async for fila in cursor}

    async def revisar(
        self, consumo_id: str, estado: str, revisado_por: str, revisado_en: datetime, comentario: str | None
    ) -> ConsumoAtipico | None:
        documento = await self._coleccion.find_one_and_update(
            {"_id": consumo_id, "estado_revision": PENDIENTE},
            {
                "$set": {
                    "estado_revision": estado,
                    "revisado_por": revisado_por,
                    "revisado_en": revisado_en,
                    "comentario": comentario,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
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
