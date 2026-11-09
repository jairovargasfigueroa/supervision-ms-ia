"""`ModeloConReintentos` (505): reintenta las fallas pasajeras del modelo de lenguaje.

Envuelve cualquier `ModeloLenguaje`. Solo reintenta `LlmNoDisponible` con `reintentable=True` (conexión, `429`,
`5xx`, `529`): un pedido rechazado (`400`), un tiempo agotado o una salida ilegible no se reintentan aquí (los maneja
el caso de uso). Hasta 2 reintentos, con esperas de 1 y 3 s.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from ia.lenguaje.aplicacion.puerto import LlmNoDisponible, ModeloLenguaje
from ia.lenguaje.dominio.mensajes import DefinicionHerramienta, Mensaje, Opciones, RespuestaModelo

registro = logging.getLogger("ia.lenguaje")
ESPERAS_S: tuple[float, ...] = (1.0, 3.0)
T = TypeVar("T")


class ModeloConReintentos:
    def __init__(self, modelo: ModeloLenguaje, esperas_s: Sequence[float] = ESPERAS_S) -> None:
        self._modelo = modelo
        self._esperas = tuple(esperas_s)

    @property
    def proveedor(self) -> str:
        return self._modelo.proveedor

    @property
    def modelo(self) -> str:
        return self._modelo.modelo

    async def conversar(
        self, mensajes: Sequence[Mensaje], herramientas: Sequence[DefinicionHerramienta], opciones: Opciones
    ) -> RespuestaModelo:
        return await self._con_reintentos("conversar", lambda: self._modelo.conversar(mensajes, herramientas, opciones))

    async def generar_json(
        self, mensajes: Sequence[Mensaje], esquema: dict[str, Any], opciones: Opciones
    ) -> dict[str, Any]:
        return await self._con_reintentos("generar_json", lambda: self._modelo.generar_json(mensajes, esquema, opciones))

    async def _con_reintentos(self, operacion: str, llamar: Callable[[], Awaitable[T]]) -> T:
        for intento, espera in enumerate((*self._esperas, None), start=1):
            try:
                return await llamar()
            except LlmNoDisponible as error:
                if not error.reintentable or espera is None:
                    raise
                registro.warning(
                    "Falla pasajera del modelo (%s, intento %d): se reintenta en %.0f s.", operacion, intento, espera
                )
                await asyncio.sleep(espera)
        raise AssertionError("inalcanzable")  # pragma: no cover
