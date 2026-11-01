"""`ia_db.ejecucion_agente` e `ia_db.sugerencia` (502, diseño «Datos»)."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from ia.agentes.aplicacion.puertos import EjecucionEnCurso
from ia.agentes.dominio.ejecucion import (
    EN_CURSO,
    FALLIDA,
    INTERRUMPIDA,
    PENDIENTE,
    PROGRAMADA,
    TERMINADA,
    Ejecucion,
    ErrorEjecucion,
    Evidencia,
    HallazgoGuardado,
    Paso,
    Sugerencia,
)
from ia.comun.mongo import IndiceMongo, registrar_indice

EJECUCIONES = "ejecucion_agente"
SUGERENCIAS = "sugerencia"

registrar_indice(
    IndiceMongo(EJECUCIONES, (("agente", 1), ("iniciada_en", DESCENDING)), "ix_ejecucion_agente_agente_iniciada")
)
registrar_indice(IndiceMongo(EJECUCIONES, (("estado", 1),), "ix_ejecucion_agente_estado"))
registrar_indice(IndiceMongo(EJECUCIONES, (("hallazgos.obra_id", 1),), "ix_ejecucion_agente_hallazgos_obra"))
# Una sola ejecución EN_CURSO por agente, aun con dos pedidos a la vez (409 EJECUCION_EN_CURSO). (decisión técnica)
registrar_indice(
    IndiceMongo(
        EJECUCIONES,
        (("agente", 1),),
        "ux_ejecucion_agente_en_curso",
        unico=True,
        filtro_parcial=(("estado", EN_CURSO),),
    )
)
registrar_indice(IndiceMongo(SUGERENCIAS, (("estado", 1), ("agente", 1)), "ix_sugerencia_estado_agente"))
registrar_indice(IndiceMongo(SUGERENCIAS, (("obra_id", 1), ("estado", 1)), "ix_sugerencia_obra_estado"))
registrar_indice(
    IndiceMongo(
        SUGERENCIAS,
        (("obra_id", 1), ("accion", 1)),
        "ux_sugerencia_pendiente_obra_accion",
        unico=True,
        filtro_parcial=(("estado", PENDIENTE),),
    )
)

Documento = dict[str, Any]


class RepositorioEjecucionesMongo:
    def __init__(self, base: AsyncDatabase[Documento]) -> None:
        self._coleccion = base[EJECUCIONES]

    async def crear(self, ejecucion: Ejecucion) -> None:
        try:
            await self._coleccion.insert_one(a_documento(ejecucion))
        except DuplicateKeyError as error:
            raise EjecucionEnCurso(ejecucion.agente) from error

    async def tomar_siguiente(self, ahora: datetime) -> Ejecucion | None:
        documento = await self._coleccion.find_one_and_update(
            {"estado": EN_CURSO, "tomada_en": None},
            {"$set": {"tomada_en": ahora}},
            sort=[("iniciada_en", ASCENDING), ("_id", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return None if documento is None else de_documento(documento)

    async def agregar_paso(self, ejecucion_id: str, paso: Paso) -> None:
        await self._coleccion.update_one({"_id": ejecucion_id}, {"$push": {"pasos": _paso_doc(paso)}})

    async def terminar(
        self, ejecucion_id: str, resumen: str, hallazgos: Sequence[HallazgoGuardado], en: datetime
    ) -> None:
        await self._coleccion.update_one(
            {"_id": ejecucion_id, "estado": EN_CURSO},
            {
                "$set": {
                    "estado": TERMINADA,
                    "resumen": resumen,
                    "hallazgos": [_hallazgo_doc(h) for h in hallazgos],
                    "terminada_en": en,
                }
            },
        )

    async def fallar(self, ejecucion_id: str, error: ErrorEjecucion, en: datetime) -> None:
        await self._coleccion.update_one(
            {"_id": ejecucion_id, "estado": EN_CURSO},
            {
                "$set": {
                    "estado": FALLIDA,
                    "error": {"codigo": error.codigo, "mensaje": error.mensaje},
                    "terminada_en": en,
                }
            },
        )

    async def recuperar_interrumpidas(self, iniciadas_antes_de: datetime, ahora: datetime) -> int:
        resultado = await self._coleccion.update_many(
            {"estado": EN_CURSO, "iniciada_en": {"$lt": iniciadas_antes_de}},
            {
                "$set": {
                    "estado": FALLIDA,
                    "error": {
                        "codigo": INTERRUMPIDA,
                        "mensaje": "La ejecución quedó en curso al reiniciar el servicio.",
                    },
                    "terminada_en": ahora,
                }
            },
        )
        return int(resultado.modified_count)

    async def obtener(self, ejecucion_id: str) -> Ejecucion | None:
        documento = await self._coleccion.find_one({"_id": ejecucion_id})
        return None if documento is None else de_documento(documento)

    async def listar(self, agente: str | None, estado: str | None, limite: int) -> list[Ejecucion]:
        filtro: Documento = {}
        if agente is not None:
            filtro["agente"] = agente
        if estado is not None:
            filtro["estado"] = estado
        cursor = self._coleccion.find(filtro).sort([("iniciada_en", DESCENDING), ("_id", DESCENDING)]).limit(limite)
        return [de_documento(d) async for d in cursor]

    async def ultima(self, agente: str) -> Ejecucion | None:
        return await self._una({"agente": agente})

    async def ultima_terminada(self, agente: str) -> Ejecucion | None:
        return await self._una({"agente": agente, "estado": TERMINADA}, orden="terminada_en")

    async def hay_programada_desde(self, agente: str, desde: datetime) -> bool:
        existe = await self._coleccion.find_one(
            {"agente": agente, "origen": PROGRAMADA, "iniciada_en": {"$gte": desde}}, projection={"_id": 1}
        )
        return existe is not None

    async def _una(self, filtro: Documento, orden: str = "iniciada_en") -> Ejecucion | None:
        cursor = self._coleccion.find(filtro).sort([(orden, DESCENDING), ("_id", DESCENDING)]).limit(1)
        async for documento in cursor:
            return de_documento(documento)
        return None


class RepositorioSugerenciasMongo:
    def __init__(self, base: AsyncDatabase[Documento]) -> None:
        self._coleccion = base[SUGERENCIAS]

    async def crear(self, sugerencia: Sugerencia) -> bool:
        try:
            await self._coleccion.insert_one(_sugerencia_doc(sugerencia))
        except DuplicateKeyError:
            return False
        return True

    async def obtener(self, sugerencia_id: str) -> Sugerencia | None:
        documento = await self._coleccion.find_one({"_id": sugerencia_id})
        return None if documento is None else _sugerencia(documento)

    async def listar(
        self, estado: str | None, agentes: Sequence[str], obra_ids: Sequence[str] | None, limite: int
    ) -> list[Sugerencia]:
        filtro: Documento = {"agente": {"$in": list(agentes)}}
        if estado is not None:
            filtro["estado"] = estado
        if obra_ids is not None:
            filtro["obra_id"] = {"$in": list(obra_ids)}
        cursor = self._coleccion.find(filtro).sort([("creada_en", DESCENDING), ("_id", DESCENDING)]).limit(limite)
        return [_sugerencia(d) async for d in cursor]

    async def contar_pendientes(self, agente: str, obra_ids: Sequence[str] | None) -> int:
        filtro: Documento = {"agente": agente, "estado": PENDIENTE}
        if obra_ids is not None:
            filtro["obra_id"] = {"$in": list(obra_ids)}
        return await self._coleccion.count_documents(filtro)

    async def decidir(
        self, sugerencia_id: str, estado: str, por: str, en: datetime, comentario: str | None
    ) -> Sugerencia | None:
        documento = await self._coleccion.find_one_and_update(
            {"_id": sugerencia_id, "estado": PENDIENTE},
            {"$set": {"estado": estado, "decidida_por": por, "decidida_en": en, "comentario": comentario}},
            return_document=ReturnDocument.AFTER,
        )
        return None if documento is None else _sugerencia(documento)


# --- conversión ------------------------------------------------------------------------------------------------------


def a_documento(e: Ejecucion) -> Documento:
    return {
        "_id": e.id,
        "agente": e.agente,
        "origen": e.origen,
        "estado": e.estado,
        "solicitada_por": e.solicitada_por,
        "iniciada_en": e.iniciada_en,
        "tomada_en": e.tomada_en,
        "terminada_en": e.terminada_en,
        "proveedor_llm": e.proveedor_llm,
        "modelo": e.modelo,
        "resumen": e.resumen,
        "error": None if e.error is None else {"codigo": e.error.codigo, "mensaje": e.error.mensaje},
        "pasos": [_paso_doc(p) for p in e.pasos],
        "hallazgos": [_hallazgo_doc(h) for h in e.hallazgos],
    }


def de_documento(d: Mapping[str, Any]) -> Ejecucion:
    error = d.get("error")
    return Ejecucion(
        id=str(d["_id"]),
        agente=str(d["agente"]),
        origen=str(d["origen"]),
        estado=str(d["estado"]),
        solicitada_por=d.get("solicitada_por"),
        iniciada_en=d["iniciada_en"],
        proveedor_llm=str(d.get("proveedor_llm") or ""),
        modelo=str(d.get("modelo") or ""),
        tomada_en=d.get("tomada_en"),
        terminada_en=d.get("terminada_en"),
        resumen=d.get("resumen"),
        error=None if not error else ErrorEjecucion(str(error["codigo"]), str(error["mensaje"])),
        pasos=tuple(
            Paso(
                numero=int(p["numero"]),
                servidor=str(p["servidor"]),
                herramienta=str(p["herramienta"]),
                argumentos=str(p["argumentos"]),
                resultado_resumen=str(p["resultado_resumen"]),
                en=p["en"],
                duracion_ms=int(p["duracion_ms"]),
                error=p.get("error"),
            )
            for p in d.get("pasos") or ()
        ),
        hallazgos=tuple(
            HallazgoGuardado(
                obra_id=str(h["obra_id"]),
                obra_nombre=str(h["obra_nombre"]),
                tipo=str(h["tipo"]),
                severidad=str(h["severidad"]),
                situacion=str(h["situacion"]),
                causa_probable=str(h["causa_probable"]),
                evidencia=tuple(Evidencia(str(x["herramienta"]), str(x["dato"])) for x in h.get("evidencia") or ()),
            )
            for h in d.get("hallazgos") or ()
        ),
    )


def _paso_doc(p: Paso) -> Documento:
    return {
        "numero": p.numero,
        "servidor": p.servidor,
        "herramienta": p.herramienta,
        "argumentos": p.argumentos,
        "resultado_resumen": p.resultado_resumen,
        "en": p.en,
        "duracion_ms": p.duracion_ms,
        "error": p.error,
    }


def _hallazgo_doc(h: HallazgoGuardado) -> Documento:
    return {
        "obra_id": h.obra_id,
        "obra_nombre": h.obra_nombre,
        "tipo": h.tipo,
        "severidad": h.severidad,
        "situacion": h.situacion,
        "causa_probable": h.causa_probable,
        "evidencia": [{"herramienta": e.herramienta, "dato": e.dato} for e in h.evidencia],
    }


def _sugerencia_doc(s: Sugerencia) -> Documento:
    return {
        "_id": s.id,
        "ejecucion_id": s.ejecucion_id,
        "agente": s.agente,
        "obra_id": s.obra_id,
        "obra_nombre": s.obra_nombre,
        "accion": s.accion,
        "descripcion": s.descripcion,
        "hallazgo_indice": s.hallazgo_indice,
        "estado": s.estado,
        "creada_en": s.creada_en,
        "decidida_por": s.decidida_por,
        "decidida_en": s.decidida_en,
        "comentario": s.comentario,
    }


def _sugerencia(d: Mapping[str, Any]) -> Sugerencia:
    return Sugerencia(
        id=str(d["_id"]),
        ejecucion_id=str(d["ejecucion_id"]),
        agente=str(d["agente"]),
        obra_id=str(d["obra_id"]),
        obra_nombre=str(d["obra_nombre"]),
        accion=str(d["accion"]),
        descripcion=str(d["descripcion"]),
        hallazgo_indice=int(d["hallazgo_indice"]),
        creada_en=d["creada_en"],
        estado=str(d["estado"]),
        decidida_por=d.get("decidida_por"),
        decidida_en=d.get("decidida_en"),
        comentario=d.get("comentario"),
    )
