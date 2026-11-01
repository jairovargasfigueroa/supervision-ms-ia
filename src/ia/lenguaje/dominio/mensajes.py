"""Lo que se conversa con el modelo de lenguaje, independiente del proveedor (Ollama o Anthropic)."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class Rol(StrEnum):
    SISTEMA = "SISTEMA"
    USUARIO = "USUARIO"
    ASISTENTE = "ASISTENTE"
    HERRAMIENTA = "HERRAMIENTA"
    """Resultado de una herramienta pedida por el modelo (502)."""


@dataclass(frozen=True)
class LlamadaHerramienta:
    id: str
    """Id para emparejar el resultado (Anthropic lo da; con Ollama lo pone el adaptador: `h1`, `h2`…)."""
    nombre: str
    argumentos: dict[str, Any]


@dataclass(frozen=True)
class Mensaje:
    rol: Rol
    contenido: str = ""
    llamadas: tuple[LlamadaHerramienta, ...] = ()
    """Solo en `ASISTENTE`: herramientas que pidió el modelo."""
    id_llamada: str | None = None
    """Solo en `HERRAMIENTA`: id de la llamada a la que responde."""
    nombre_herramienta: str | None = None
    """Solo en `HERRAMIENTA`."""
    es_error: bool = False
    """Solo en `HERRAMIENTA`: el resultado fue un error (`isError`)."""
    crudo: tuple[dict[str, Any], ...] = field(default=(), compare=False)
    """Solo en `ASISTENTE`: bloques tal como los devolvió el proveedor (Anthropic exige devolver los de razonamiento
    junto con las llamadas a herramientas). El código no los interpreta."""

    @staticmethod
    def sistema(texto: str) -> "Mensaje":
        return Mensaje(Rol.SISTEMA, texto)

    @staticmethod
    def usuario(texto: str) -> "Mensaje":
        return Mensaje(Rol.USUARIO, texto)

    @staticmethod
    def resultado(llamada: LlamadaHerramienta, contenido: str, es_error: bool = False) -> "Mensaje":
        return Mensaje(
            Rol.HERRAMIENTA, contenido, id_llamada=llamada.id, nombre_herramienta=llamada.nombre, es_error=es_error
        )


@dataclass(frozen=True)
class DefinicionHerramienta:
    nombre: str
    descripcion: str
    esquema: dict[str, Any]
    """JSON Schema de la entrada (el que publicó el servidor MCP, ya filtrado por agente)."""


@dataclass(frozen=True)
class RespuestaModelo:
    texto: str
    llamadas: tuple[LlamadaHerramienta, ...]
    mensaje: Mensaje
    """El mensaje `ASISTENTE` para agregar a la conversación (con los bloques crudos del proveedor)."""
    tokens_entrada: int | None = None
    tokens_salida: int | None = None


Esfuerzo = Literal["BAJO", "MEDIO"]


@dataclass(frozen=True)
class Opciones:
    tiempo_maximo_s: float
    temperatura: float | None = None
    """Solo Ollama (Anthropic no recibe `temperature`)."""
    esfuerzo: Esfuerzo = "BAJO"
    """Solo Anthropic: `low` para interpretar y resumir, `medium` para los agentes."""
    max_tokens: int = 4096
    contexto: str | None = None
    """`consultaId` o `ejecucionId`, solo para el registro."""
