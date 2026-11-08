"""`ia_db.consulta_reporte` (503): cada consulta de reporte de las personas, también las no reconocidas."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pymongo import DESCENDING
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from ia.comun.mongo import IndiceMongo, registrar_indice
from ia.constructor_reportes.aplicacion.puertos import ConsultaGuardada, ReportesNoDisponibles

COLECCION = "consulta_reporte"

registrar_indice(
    IndiceMongo(COLECCION, (("usuario_id", 1), ("creada_en", DESCENDING)), "ix_consulta_reporte_usuario_creada")
)


class RepositorioConsultasMongo:
    def __init__(self, base: AsyncDatabase[dict[str, Any]]) -> None:
        self._coleccion = base[COLECCION]

    async def guardar(self, consulta: ConsultaGuardada) -> None:
        try:
            await self._coleccion.insert_one(_documento(consulta))
        except PyMongoError as error:
            raise ReportesNoDisponibles("ia_db no respondió.") from error

    async def ultimas(self, usuario_id: str, limite: int) -> Sequence[ConsultaGuardada]:
        try:
            cursor = (
                self._coleccion.find({"usuario_id": usuario_id})
                .sort([("creada_en", DESCENDING), ("_id", DESCENDING)])
                .limit(limite)
            )
            return [_consulta(d) async for d in cursor]
        except PyMongoError as error:
            raise ReportesNoDisponibles("ia_db no respondió.") from error


def _documento(c: ConsultaGuardada) -> dict[str, Any]:
    return {
        "_id": c.id,
        "usuario_id": c.usuario_id,
        "texto": c.texto,
        "atajo": c.atajo,
        "conjunto": c.conjunto,
        "receta": c.receta,
        "resumen": c.resumen,
        "resumen_plantilla": c.resumen_plantilla,
        "resultado": c.resultado,
        "error_codigo": c.error_codigo,
        "total_filas": c.total_filas,
        "proveedor_llm": c.proveedor_llm,
        "modelo": c.modelo,
        "duracion_ms": c.duracion_ms,
        "creada_en": c.creada_en,
    }


def _consulta(d: dict[str, Any]) -> ConsultaGuardada:
    creada: datetime = d["creada_en"]
    return ConsultaGuardada(
        id=str(d["_id"]),
        usuario_id=str(d["usuario_id"]),
        texto=d.get("texto"),
        atajo=d.get("atajo"),
        conjunto=d.get("conjunto"),
        receta=d.get("receta"),
        resumen=d.get("resumen"),
        resumen_plantilla=bool(d.get("resumen_plantilla")),
        resultado=str(d["resultado"]),
        error_codigo=d.get("error_codigo"),
        total_filas=int(d.get("total_filas") or 0),
        proveedor_llm=str(d.get("proveedor_llm") or ""),
        modelo=str(d.get("modelo") or ""),
        duracion_ms=int(d.get("duracion_ms") or 0),
        creada_en=creada if creada.tzinfo else creada.replace(tzinfo=UTC),
    )
