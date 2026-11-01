"""Consulta de `ia_db.consumo_atipico` (403) para el conjunto `CONSUMOS_ATIPICOS`.

El filtro (`$match`) y la agregación (`$group`, `$sort`, `$limit`) se arman con nombres de campo **constantes**; los
valores ya validados son textos, fechas o listas de textos (Pydantic rechaza objetos), así que no puede entrar un
operador como `$ne`. Solo `aggregate` sobre `consumo_atipico`, con `maxTimeMS` de 5 s. Los filtros de obra, zona y
cliente se resuelven antes en `rpt_obra_resumen` (usuario lector).
"""

import logging
from collections.abc import Sequence
from datetime import datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from bson.decimal128 import Decimal128
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from ia.comun.tiempo import ZONA_NEGOCIO
from ia.constructor_reportes.aplicacion.puertos import ConsultaResuelta, FilasConsulta, ReportesNoDisponibles
from ia.constructor_reportes.dominio.validacion import FiltroValidado

registro = logging.getLogger("ia.constructor_reportes")

COLECCION = "consumo_atipico"
TIEMPO_MAXIMO_MS = 5000
MOTIVOS_MAXIMO = 300

# Clave del catálogo → campo de la colección.
_CAMPOS = {
    "obra": "obra_id",
    "material": "material_id",
    "periodo": "periodo",
    "estado_revision": "estado_revision",
    "fecha": "fecha",
    "cantidad": "cantidad",
    "puntaje": "puntaje",
    "motivos": "motivos",
}
_FILTROS = {
    "material": "material_id",
    "materialId": "material_id",
    "estadoRevision": "estado_revision",
    "periodo": "periodo",
}
_ACUMULADORES = {"CONTEO": "$sum", "SUMA": "$sum", "PROMEDIO": "$avg", "MAXIMO": "$max", "MINIMO": "$min"}
_RANGO_OBRA = "_rango_obra"


class ObrasLector(Protocol):
    async def obras_filtradas(self, consulta: ConsultaResuelta) -> list[str] | None: ...

    async def nombres_obras(self, obra_ids: Sequence[str]) -> dict[str, str]: ...

    async def obras_por_nombre(self) -> list[str]: ...


class ConsultaAtipicosMongo:
    def __init__(self, base: AsyncDatabase[dict[str, Any]], obras: ObrasLector, zona: str = ZONA_NEGOCIO) -> None:
        self._coleccion = base[COLECCION]
        self._obras = obras
        self._zona = zona

    async def ejecutar(self, consulta: ConsultaResuelta) -> FilasConsulta:
        receta = consulta.receta
        claves = receta.claves_salida()
        obra_ids = await self._obras.obras_filtradas(consulta)
        if obra_ids is not None and not obra_ids:
            return FilasConsulta(claves, [], False)
        filtro = self._filtro(consulta, obra_ids)
        ordena_por_obra = any(por == "obra" for por, _ in receta.orden)
        tuberia: list[dict[str, Any]] = [
            {"$match": filtro},
            {
                "$addFields": {
                    "fecha": {"$dateToString": {"format": "%Y-%m-%d", "date": "$registrado_en", "timezone": self._zona}}
                }
            },
        ]
        if ordena_por_obra:
            # Orden por nombre de obra: posición del id en la lista de obras ordenada por nombre (de rpt_obra_resumen).
            tuberia.append(
                {"$addFields": {_RANGO_OBRA: {"$indexOfArray": [await self._obras.obras_por_nombre(), "$obra_id"]}}}
            )
        if receta.agrupada:
            grupo: dict[str, Any] = {"_id": {d: f"${_CAMPOS[d]}" for d in receta.agrupar_por}}
            for metrica in receta.metricas:
                valor: Any = 1 if metrica.funcion == "CONTEO" else f"${_CAMPOS[str(metrica.campo)]}"
                grupo[metrica.clave] = {_ACUMULADORES[metrica.funcion]: valor}
            if ordena_por_obra:
                grupo[_RANGO_OBRA] = {"$first": f"${_RANGO_OBRA}"}
            tuberia.append({"$group": grupo})
            orden = {self._campo_orden(por, agrupada=True): (1 if d == "ASC" else -1) for por, d in receta.orden}
        else:
            orden = {self._campo_orden(por, agrupada=False): (1 if d == "ASC" else -1) for por, d in receta.orden}
        orden.setdefault("_id", 1)
        tuberia += [{"$sort": orden}, {"$limit": receta.limite + 1}]
        try:
            cursor = await self._coleccion.aggregate(tuberia, maxTimeMS=TIEMPO_MAXIMO_MS)
            documentos = [d async for d in cursor]
        except PyMongoError as error:
            registro.warning("ia_db no respondió: %s", type(error).__name__)
            raise ReportesNoDisponibles("ia_db no respondió.") from error

        truncado = len(documentos) > receta.limite
        documentos = documentos[: receta.limite]
        filas = [self._fila(d, claves, receta.agrupada) for d in documentos]
        if "obra" in claves:
            i = claves.index("obra")
            nombres = await self._obras.nombres_obras(sorted({str(f[i]) for f in filas if f[i] is not None}))
            filas = [(*f[:i], nombres.get(str(f[i]), f[i]), *f[i + 1 :]) for f in filas]
        return FilasConsulta(claves, filas, truncado)

    def _filtro(self, consulta: ConsultaResuelta, obra_ids: list[str] | None) -> dict[str, Any]:
        filtro: dict[str, Any] = {}
        if obra_ids is not None:
            filtro["obra_id"] = {"$in": list(obra_ids)}
        zona = ZoneInfo(self._zona)
        for f in consulta.receta.filtros:
            if f.campo == "material" and f.operador == "CONTIENE":
                _y(filtro, "material_id", {"$in": list(consulta.ids_material or ())})
            elif f.campo == "fecha":
                dia = f.valor
                assert not isinstance(dia, str | bool | int | tuple)
                if f.operador == "DESDE":
                    _y(filtro, "registrado_en", {"$gte": datetime.combine(dia, time(), zona)})
                else:
                    _y(filtro, "registrado_en", {"$lt": datetime.combine(dia + timedelta(days=1), time(), zona)})
            elif f.campo in _FILTROS:
                _y(filtro, _FILTROS[f.campo], _condicion(f))
        return filtro

    @staticmethod
    def _campo_orden(por: str, *, agrupada: bool) -> str:
        if por == "obra":
            return _RANGO_OBRA
        if agrupada:
            return f"_id.{por}" if por in _CAMPOS and por not in ("cantidad", "puntaje") else por
        return "registrado_en" if por == "fecha" else _CAMPOS[por]

    @staticmethod
    def _fila(documento: dict[str, Any], claves: Sequence[str], agrupada: bool) -> tuple[Any, ...]:
        valores = []
        for clave in claves:
            if agrupada and clave in _CAMPOS:
                valor = documento["_id"].get(clave)
            elif clave == "motivos":
                valor = "; ".join(str(m) for m in documento.get("motivos") or [])[:MOTIVOS_MAXIMO]
            else:
                valor = documento.get(_CAMPOS.get(clave, clave))
            valores.append(valor.to_decimal() if isinstance(valor, Decimal128) else valor)
        return tuple(valores)


def _condicion(f: FiltroValidado) -> Any:
    """Valor ya validado (texto o lista de textos): nunca un objeto que venga del modelo."""
    if f.operador == "EN":
        assert isinstance(f.valor, tuple)
        return {"$in": [str(v) for v in f.valor]}
    assert isinstance(f.valor, str)
    if f.operador == "DESDE":
        return {"$gte": f.valor}
    if f.operador == "HASTA":
        return {"$lte": f.valor}
    return {"$eq": f.valor}


def _y(filtro: dict[str, Any], campo: str, condicion: dict[str, Any]) -> None:
    """Junta condiciones sobre el mismo campo (por ejemplo, periodo desde y hasta)."""
    filtro.setdefault(campo, {}).update(condicion)
