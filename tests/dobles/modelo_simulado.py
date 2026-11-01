"""`ModeloSimulado`: modelo de lenguaje con respuestas guionadas (las pruebas nunca llaman a un modelo real).

Cada respuesta del guion puede ser:
- para `generar_json`: un `dict`, una excepción (se lanza) o una función `(mensajes, esquema) -> dict`;
- para `conversar`: una `RespuestaModelo`, una lista de `LlamadaHerramienta` (sin texto), un `str` (solo texto), una
  excepción o una función `(mensajes, herramientas) -> RespuestaModelo`.
Si el guion se acaba, `generar_json` lanza `LlmNoDisponible` (como un modelo caído) y `conversar` responde «sin más
llamadas».
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ia.lenguaje.aplicacion.puerto import LlmNoDisponible, LlmTiempoAgotado
from ia.lenguaje.dominio.mensajes import (
    DefinicionHerramienta,
    LlamadaHerramienta,
    Mensaje,
    Opciones,
    RespuestaModelo,
    Rol,
)


@dataclass
class LlamadaRegistrada:
    operacion: str
    mensajes: list[Mensaje]
    opciones: Opciones
    esquema: dict[str, Any] | None = None
    herramientas: list[DefinicionHerramienta] = field(default_factory=list)


class ModeloSimulado:
    def __init__(
        self,
        json_: Sequence[Any] = (),
        conversacion: Sequence[Any] = (),
        *,
        proveedor: str = "ollama",
        modelo: str = "simulado",
        demora_s: float = 0.0,
    ) -> None:
        self._json = list(json_)
        self._conversacion = list(conversacion)
        self._proveedor = proveedor
        self._modelo = modelo
        self.demora_s = demora_s
        self.llamadas: list[LlamadaRegistrada] = []

    @property
    def proveedor(self) -> str:
        return self._proveedor

    @property
    def modelo(self) -> str:
        return self._modelo

    def llamadas_de(self, operacion: str) -> list[LlamadaRegistrada]:
        return [llamada for llamada in self.llamadas if llamada.operacion == operacion]

    async def generar_json(
        self, mensajes: Sequence[Mensaje], esquema: dict[str, Any], opciones: Opciones
    ) -> dict[str, Any]:
        self.llamadas.append(LlamadaRegistrada("generar_json", list(mensajes), opciones, esquema=esquema))
        await self._esperar(opciones)
        if not self._json:
            raise LlmNoDisponible("El guion de generar_json se acabó.")
        siguiente = self._json.pop(0)
        if isinstance(siguiente, BaseException):
            raise siguiente
        if callable(siguiente):
            resultado: dict[str, Any] = siguiente(list(mensajes), esquema)
            return resultado
        assert isinstance(siguiente, dict)
        return dict(siguiente)

    async def conversar(
        self, mensajes: Sequence[Mensaje], herramientas: Sequence[DefinicionHerramienta], opciones: Opciones
    ) -> RespuestaModelo:
        self.llamadas.append(LlamadaRegistrada("conversar", list(mensajes), opciones, herramientas=list(herramientas)))
        await self._esperar(opciones)
        if not self._conversacion:
            return _texto("Sin más llamadas.")
        siguiente = self._conversacion.pop(0)
        if isinstance(siguiente, BaseException):
            raise siguiente
        if isinstance(siguiente, RespuestaModelo):
            return siguiente
        if isinstance(siguiente, str):
            return _texto(siguiente)
        if isinstance(siguiente, list):
            previas = sum(len(m.llamadas) for m in mensajes)
            llamadas = tuple(
                LlamadaHerramienta(f"s{previas + i}", c.nombre, c.argumentos) if not c.id else c
                for i, c in enumerate(siguiente, start=1)
            )
            return RespuestaModelo("", llamadas, Mensaje(Rol.ASISTENTE, "", llamadas=llamadas))
        funcion: Callable[..., RespuestaModelo] = siguiente
        return funcion(list(mensajes), list(herramientas))

    async def _esperar(self, opciones: Opciones) -> None:
        if self.demora_s:
            try:
                await asyncio.wait_for(asyncio.sleep(self.demora_s), timeout=opciones.tiempo_maximo_s)
            except TimeoutError as error:
                raise LlmTiempoAgotado("El modelo simulado no respondió a tiempo.") from error


def llamada(nombre: str, **argumentos: Any) -> LlamadaHerramienta:
    """Llamada pedida por el modelo simulado (el id lo pone el doble)."""
    return LlamadaHerramienta("", nombre, argumentos)


def _texto(texto: str) -> RespuestaModelo:
    return RespuestaModelo(texto, (), Mensaje(Rol.ASISTENTE, texto))
