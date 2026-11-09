"""`ia_db.conversacion_reporte` y `ia_db.receta_cache` (505).

- `conversacion_reporte`: estado de cada conversación del constructor; índice TTL en `expira_en` (30 min sin uso,
  se renueva en cada turno).
- `receta_cache`: receta validada por pedido normalizado y día; índice TTL en `expira_en` (24 h).
"""

from datetime import UTC, datetime
from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from ia.comun.mongo import IndiceMongo, registrar_indice
from ia.constructor_reportes.aplicacion.puertos import ReportesNoDisponibles
from ia.constructor_reportes.dominio.conversacion import Conversacion, Duda, Turno

CONVERSACIONES = "conversacion_reporte"
CACHE = "receta_cache"

registrar_indice(IndiceMongo(CONVERSACIONES, (("expira_en", 1),), "ttl_conversacion_reporte", expira_despues_s=0))
registrar_indice(IndiceMongo(CACHE, (("expira_en", 1),), "ttl_receta_cache", expira_despues_s=0))


def _utc(instante: datetime) -> datetime:
    return instante if instante.tzinfo else instante.replace(tzinfo=UTC)


class RepositorioConversacionesMongo:
    def __init__(self, base: AsyncDatabase[dict[str, Any]]) -> None:
        self._coleccion = base[CONVERSACIONES]

    async def obtener(self, conversacion_id: str) -> Conversacion | None:
        try:
            documento = await self._coleccion.find_one({"_id": conversacion_id})
        except PyMongoError as error:
            raise ReportesNoDisponibles("ia_db no respondió.") from error
        # El TTL de MongoDB borra cada minuto: el caso de uso revisa además `expira_en` con su reloj.
        return None if documento is None else _conversacion(documento)

    async def guardar(self, conversacion: Conversacion) -> None:
        try:
            await self._coleccion.replace_one({"_id": conversacion.id}, _documento(conversacion), upsert=True)
        except PyMongoError as error:
            raise ReportesNoDisponibles("ia_db no respondió.") from error


class CacheRecetasMongo:
    def __init__(self, base: AsyncDatabase[dict[str, Any]]) -> None:
        self._coleccion = base[CACHE]

    async def obtener(self, clave: str) -> dict[str, Any] | None:
        try:
            documento = await self._coleccion.find_one({"_id": clave})
        except PyMongoError:
            return None  # la caché es opcional: sin ella se interpreta con el modelo
        if documento is None:
            return None
        receta: dict[str, Any] = documento["receta"]
        return receta

    async def guardar(self, clave: str, receta: dict[str, Any], expira_en: datetime) -> None:
        try:
            await self._coleccion.replace_one(
                {"_id": clave}, {"_id": clave, "receta": receta, "expira_en": expira_en}, upsert=True
            )
        except PyMongoError:
            return


def _documento(c: Conversacion) -> dict[str, Any]:
    return {
        "_id": c.id,
        "usuario_id": c.usuario_id,
        "turnos": [{"rol": t.rol, "texto": t.texto, "tipo": t.tipo, "en": t.en} for t in c.turnos],
        "receta": c.receta,
        "preguntas": c.preguntas,
        "duda": None
        if c.duda is None
        else {
            "campo": c.duda.campo,
            "termino": c.duda.termino,
            "clase": c.duda.clase,
            "opciones": list(c.duda.opciones),
            "receta": c.duda.receta,
        },
        "aceptados": list(c.aceptados),
        "creada_en": c.creada_en,
        "actualizada_en": c.actualizada_en,
        "expira_en": c.expira_en,
    }


def _conversacion(d: dict[str, Any]) -> Conversacion:
    duda = d.get("duda")
    return Conversacion(
        id=str(d["_id"]),
        usuario_id=str(d["usuario_id"]),
        creada_en=_utc(d["creada_en"]),
        actualizada_en=_utc(d["actualizada_en"]),
        turnos=[Turno(t["rol"], t["texto"], t["tipo"], _utc(t["en"])) for t in d.get("turnos", [])],
        receta=d.get("receta"),
        preguntas=int(d.get("preguntas") or 0),
        duda=None
        if not duda
        else Duda(duda["campo"], duda["termino"], duda["clase"], tuple(duda["opciones"]), duda["receta"]),
        aceptados=list(d.get("aceptados") or []),
    )
