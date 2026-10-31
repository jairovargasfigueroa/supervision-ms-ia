"""Colección `ia_db.prediccion` (402): historial de predicciones, idempotente por `clave`."""

from datetime import UTC, datetime
from typing import Any

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase

from ia.comun.mongo import IndiceMongo, registrar_indice
from ia.riesgo.aplicacion.puertos import Prediccion
from ia.riesgo.dominio.variables import SIN_DATOS, Factor

COLECCION = "prediccion"

registrar_indice(IndiceMongo(COLECCION, (("clave", 1),), "ux_prediccion_clave", unico=True))
registrar_indice(IndiceMongo(COLECCION, (("obra_id", 1), ("calculada_en", DESCENDING)), "ix_prediccion_obra_calculada"))


class AlmacenPrediccionesMongo:
    def __init__(self, base: AsyncDatabase[dict[str, Any]]) -> None:
        self._coleccion = base[COLECCION]

    async def guardar(self, prediccion: Prediccion) -> bool:
        resultado = await self._coleccion.update_one(
            {"clave": prediccion.clave}, {"$setOnInsert": _documento(prediccion)}, upsert=True
        )
        return resultado.upserted_id is not None

    async def ultimas(self, obra_ids: list[str]) -> dict[str, Prediccion]:
        if not obra_ids:
            return {}
        tuberia: list[dict[str, Any]] = [
            {"$match": {"obra_id": {"$in": obra_ids}}},
            {"$sort": {"obra_id": 1, "calculada_en": -1, "_id": -1}},
            {"$group": {"_id": "$obra_id", "documento": {"$first": "$$ROOT"}}},
        ]
        cursor = await self._coleccion.aggregate(tuberia)
        return {fila["_id"]: _prediccion(fila["documento"]) async for fila in cursor}

    async def historial(self, obra_id: str, limite: int) -> list[Prediccion]:
        cursor = (
            self._coleccion.find({"obra_id": obra_id, "nivel": {"$ne": SIN_DATOS}})
            .sort([("calculada_en", DESCENDING), ("_id", DESCENDING)])
            .limit(limite)
        )
        recientes = [_prediccion(documento) async for documento in cursor]
        return list(reversed(recientes))


def _documento(p: Prediccion) -> dict[str, Any]:
    return {
        "clave": p.clave,
        "obra_id": p.obra_id,
        "riesgo_retraso": p.riesgo_retraso,
        "nivel": p.nivel,
        "factores": [{"variable": f.variable, "descripcion": f.descripcion, "peso": f.peso} for f in p.factores],
        "variables": dict(p.variables),
        "version_modelo": p.version_modelo,
        "motivo": p.motivo,
        "calculada_en": p.calculada_en,
    }


def _prediccion(d: dict[str, Any]) -> Prediccion:
    calculada: datetime = d["calculada_en"]
    return Prediccion(
        clave=d["clave"],
        obra_id=d["obra_id"],
        riesgo_retraso=d.get("riesgo_retraso"),
        nivel=d["nivel"],
        factores=tuple(Factor(f["variable"], f["descripcion"], f["peso"]) for f in d.get("factores", [])),
        variables=d.get("variables", {}),
        version_modelo=d.get("version_modelo"),
        motivo=d["motivo"],
        calculada_en=calculada.astimezone(UTC) if calculada.tzinfo else calculada.replace(tzinfo=UTC),
    )
