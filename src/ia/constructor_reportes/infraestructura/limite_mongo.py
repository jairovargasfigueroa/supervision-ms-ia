"""`ia_db.limite_consulta` (503): 10 consultas de reporte por minuto por usuario (ventana fija de un minuto, decisión
del equipo). `find_one_and_update` con `$inc` y `upsert` es atómico y sirve con varios procesos de `ia-api`."""

from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError, PyMongoError

from ia.comun.mongo import IndiceMongo, registrar_indice
from ia.constructor_reportes.aplicacion.puertos import ReportesNoDisponibles

COLECCION = "limite_consulta"

registrar_indice(IndiceMongo(COLECCION, (("expira_en", 1),), "ttl_limite_consulta_expira", expira_despues_s=0))


class LimitadorConsultasMongo:
    def __init__(self, base: AsyncDatabase[dict[str, Any]]) -> None:
        self._coleccion = base[COLECCION]

    async def contar(self, usuario_id: str, ahora: datetime) -> tuple[int, int]:
        minuto = ahora.astimezone(UTC).replace(second=0, microsecond=0)
        siguiente = minuto + timedelta(minutes=1)
        clave = f"{usuario_id}:{minuto.strftime('%Y-%m-%dT%H:%M')}"
        actualizacion = {
            "$inc": {"cuenta": 1},
            "$setOnInsert": {"usuario_id": usuario_id, "expira_en": siguiente + timedelta(minutes=2)},
        }
        try:
            try:
                documento = await self._incrementar(clave, actualizacion)
            except DuplicateKeyError:
                # Dos upsert a la vez sobre un _id nuevo: el segundo choca; al reintentar ya existe.
                documento = await self._incrementar(clave, actualizacion)
        except PyMongoError as error:
            raise ReportesNoDisponibles("ia_db no respondió.") from error
        segundos = max(1, int((siguiente - ahora.astimezone(UTC)).total_seconds() + 0.999))
        return int(documento["cuenta"]), segundos

    async def _incrementar(self, clave: str, actualizacion: dict[str, Any]) -> dict[str, Any]:
        documento = await self._coleccion.find_one_and_update(
            {"_id": clave}, actualizacion, upsert=True, return_document=ReturnDocument.AFTER
        )
        assert documento is not None  # con upsert y AFTER siempre hay documento
        return documento
