"""Consultas del riesgo (no calculan nada: leen `ia_db.prediccion` y la base de reportes)."""

from dataclasses import dataclass

from ia.auth.usuario import Usuario
from ia.auth.visibilidad import residente_filtrado
from ia.riesgo.aplicacion.puertos import AlmacenPredicciones, InfoModelo, ModeloRiesgo, Prediccion, VisibilidadObras

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
