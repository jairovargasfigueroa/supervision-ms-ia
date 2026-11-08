"""Consultas del riesgo (no calculan nada: leen `ia_db.prediccion` y la base de reportes)."""

from dataclasses import dataclass

from ia.auth.usuario import Usuario
from ia.auth.visibilidad import residente_filtrado
from ia.riesgo.aplicacion.puertos import (
    AlmacenPredicciones,
    InfoModelo,
    ModeloRiesgo,
    ObraResumen,
    ObrasResumen,
    Prediccion,
    VisibilidadObras,
)
from ia.riesgo.dominio.variables import SIN_DATOS

MAXIMO_OBRAS = 100


class DemasiadasObras(Exception):
    pass


class ObraNoEncontrada(Exception):
    pass


class ModeloNoDisponible(Exception):
    pass


@dataclass(frozen=True)
class PrediccionConHistorial:
    prediccion: Prediccion | None
    """`None` si la obra todavía no tiene predicción (se responde `SIN_DATOS`)."""
    historial: list[Prediccion]


class ConsultasRiesgo:
    def __init__(
        self, visibilidad: VisibilidadObras, almacen: AlmacenPredicciones, modelo: ModeloRiesgo | None
    ) -> None:
        self._visibilidad = visibilidad
        self._almacen = almacen
        self._modelo = modelo

    async def predicciones(self, usuario: Usuario, obra_ids: list[str]) -> list[Prediccion]:
        """Última predicción de cada obra visible pedida, en el orden pedido; las demás se omiten."""
        ids = list(dict.fromkeys(obra_ids))
        if len(ids) > MAXIMO_OBRAS:
            raise DemasiadasObras()
        visibles = await self._visibilidad.visibles(ids, residente_filtrado(usuario))
        ultimas = await self._almacen.ultimas([i for i in ids if i in visibles])
        return [ultimas[i] for i in ids if i in ultimas]

    async def prediccion_obra(self, usuario: Usuario, obra_id: str, historial: int) -> PrediccionConHistorial:
        if obra_id not in await self._visibilidad.visibles([obra_id], residente_filtrado(usuario)):
            raise ObraNoEncontrada()
        ultima = (await self._almacen.ultimas([obra_id])).get(obra_id)
        if ultima is None:
            return PrediccionConHistorial(None, [])
        return PrediccionConHistorial(ultima, await self._almacen.historial(obra_id, historial))

    def modelo(self) -> InfoModelo:
        if self._modelo is None:
            raise ModeloNoDisponible()
        return self._modelo.info


@dataclass(frozen=True)
class PrediccionObra:
    obra: ObraResumen
    prediccion: Prediccion


@dataclass(frozen=True)
class PrediccionesFiltradas:
    predicciones: list[PrediccionObra]
    """Hasta `limite`, de la probabilidad más alta a la más baja (las `SIN_DATOS` al final)."""
    total: int
    """Las que cumplen el filtro, antes de recortar."""


class PrediccionesEnEjecucion:
    """Última predicción de las obras `EN_EJECUCION` (herramienta MCP `riesgo_retraso`, 501). Solo lee."""

    def __init__(self, obras: ObrasResumen, almacen: AlmacenPredicciones) -> None:
        self._obras = obras
        self._almacen = almacen

    async def consultar(
        self, obra_ids: list[str] | None, niveles: frozenset[str], limite: int
    ) -> PrediccionesFiltradas:
        """`ObraNoEncontrada` si un id pedido no está en la base de reportes. Las obras sin predicción se omiten."""
        if obra_ids is not None:
            pedidas = list(dict.fromkeys(obra_ids))
            existentes = await self._obras.existentes(pedidas)
            faltantes = [i for i in pedidas if i not in existentes]
            if faltantes:
                raise ObraNoEncontrada(faltantes[0])
            obras = {i: o for i, o in existentes.items() if o.estado == "EN_EJECUCION"}
        else:
            obras = await self._obras.en_ejecucion()
        ultimas = await self._almacen.ultimas(sorted(obras))
        filtradas = [PrediccionObra(obras[i], p) for i, p in ultimas.items() if p.nivel in niveles]
        filtradas.sort(
            key=lambda x: (
                x.prediccion.nivel == SIN_DATOS or x.prediccion.riesgo_retraso is None,
                -(x.prediccion.riesgo_retraso or 0.0),
                x.obra.nombre,
                x.obra.obra_id,
            )
        )
        return PrediccionesFiltradas(filtradas[:limite], len(filtradas))
