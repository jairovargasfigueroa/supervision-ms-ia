"""Puerto del modelo de lenguaje (lo usan 503 y 502) y sus errores.

La salida de `generar_json` **siempre** se vuelve a validar con Pydantic en el código que la pide: el proveedor
ayuda a respetar el esquema, pero no es la defensa (504).
"""

import logging
from collections.abc import Sequence
from typing import Any, Protocol

from ia.lenguaje.dominio.mensajes import DefinicionHerramienta, Mensaje, Opciones, RespuestaModelo

registro = logging.getLogger("ia.lenguaje")


class ErrorLlm(Exception):
    """Falla del modelo de lenguaje."""


class LlmNoDisponible(ErrorLlm):
    """Conexión, `5xx`, `429` o «sobrecargado» del proveedor."""


class LlmTiempoAgotado(ErrorLlm):
    """Venció `Opciones.tiempo_maximo_s`."""


class SalidaNoValida(ErrorLlm):
    """La respuesta no es un JSON que se pueda leer (o no es un objeto)."""


class ModeloLenguaje(Protocol):
    @property
    def proveedor(self) -> str:
        """`ollama` o `anthropic`."""
        ...

    @property
    def modelo(self) -> str:
        """`IA_LLM_MODELO` (por ejemplo `qwen3:8b` o `claude-sonnet-5-5`)."""
        ...

    async def conversar(
        self, mensajes: Sequence[Mensaje], herramientas: Sequence[DefinicionHerramienta], opciones: Opciones
    ) -> RespuestaModelo:
        """Texto y/o llamadas a herramientas (502)."""
        ...

    async def generar_json(
        self, mensajes: Sequence[Mensaje], esquema: dict[str, Any], opciones: Opciones
    ) -> dict[str, Any]:
        """Salida estructurada con el JSON Schema dado (503 y el reporte de la 502)."""
        ...


def registrar_llamada(
    *,
    proveedor: str,
    modelo: str,
    operacion: str,
    duracion_ms: int,
    resultado: str,
    opciones: Opciones,
    tokens_entrada: int | None = None,
    tokens_salida: int | None = None,
) -> None:
    """Una línea por llamada al modelo (504): nunca el texto de los mensajes, la clave de la API ni tokens."""
    registro.info(
        "Llamada al modelo: proveedor=%s modelo=%s operacion=%s contexto=%s duracionMs=%d tokensEntrada=%s "
        "tokensSalida=%s resultado=%s",
        proveedor,
        modelo,
        operacion,
        opciones.contexto or "-",
        duracion_ms,
        "-" if tokens_entrada is None else tokens_entrada,
        "-" if tokens_salida is None else tokens_salida,
        resultado,
    )


def registrar_validacion(*, contexto: str | None, operacion: str, valida: bool) -> None:
    """Si la salida del modelo pasó la validación del código que la pidió (Pydantic y reglas)."""
    registro.info(
        "Validación de la salida del modelo: operacion=%s contexto=%s valida=%s", operacion, contexto or "-", valida
    )
