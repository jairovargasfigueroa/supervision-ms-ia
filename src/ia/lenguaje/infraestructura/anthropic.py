"""Adaptador de Anthropic (SDK oficial `anthropic` 2, `AsyncAnthropic`, Messages API).

- Herramientas `{name, description, input_schema, strict: true}`; los resultados vuelven como `tool_result` con el
  `tool_use_id`. Salida estructurada con `output_config.format = {type: "json_schema", schema}`.
- Razonamiento `thinking: {type: "between_tools"}` y `output_config.effort` (`low` o `medium`); no se envía
  `temperature`. Los bloques de la respuesta se guardan crudos para devolverlos tal cual en la vuelta siguiente.
- Con este proveedor, el texto del usuario y los datos delimitados salen a la API de Anthropic.
- El SDK 2 usa `httpx2` (no `httpx`): el transporte simulado de las pruebas es el de `httpx2`.
"""

import asyncio
import time
from collections.abc import Sequence
from typing import Any

import anthropic
import httpx2
from anthropic import AsyncAnthropic
from anthropic.types import Message

from ia.lenguaje.aplicacion.puerto import LlmNoDisponible, LlmTiempoAgotado, registrar_llamada
from ia.lenguaje.dominio.mensajes import (
    DefinicionHerramienta,
    LlamadaHerramienta,
    Mensaje,
    Opciones,
    RespuestaModelo,
    Rol,
)
from ia.lenguaje.infraestructura.esquemas import adaptar_esquema
from ia.lenguaje.infraestructura.ollama import leer_objeto_json

PROVEEDOR = "anthropic"
_ESFUERZO = {"BAJO": "low", "MEDIO": "medium"}


class ModeloAnthropic:
    def __init__(self, clave: str, modelo: str, transporte: httpx2.AsyncBaseTransport | None = None) -> None:
        """`transporte`: solo en las pruebas. Sin reintentos del SDK: los tiempos máximos y los respaldos los maneja
        cada caso de uso."""
        self._modelo = modelo
        http = None if transporte is None else httpx2.AsyncClient(transport=transporte)
        self._cliente = AsyncAnthropic(api_key=clave, max_retries=0, http_client=http)

    @property
    def proveedor(self) -> str:
        return PROVEEDOR

    @property
    def modelo(self) -> str:
        return self._modelo

    async def conversar(
        self, mensajes: Sequence[Mensaje], herramientas: Sequence[DefinicionHerramienta], opciones: Opciones
    ) -> RespuestaModelo:
        extra: dict[str, Any] = {}
        if herramientas:
            extra["tools"] = [
                {
                    "name": h.nombre,
                    "description": h.descripcion,
                    "input_schema": adaptar_esquema(h.esquema, PROVEEDOR),
                    "strict": True,
                }
                for h in herramientas
            ]
        respuesta = await self._crear("conversar", mensajes, opciones, {"effort": _ESFUERZO[opciones.esfuerzo]}, extra)
        texto = _texto(respuesta)
        llamadas = tuple(
            LlamadaHerramienta(id=b.id, nombre=b.name, argumentos=dict(b.input))
            for b in respuesta.content
            if b.type == "tool_use"
        )
        crudo = tuple(b.model_dump(mode="json", exclude_none=True) for b in respuesta.content)
        return RespuestaModelo(
            texto=texto,
            llamadas=llamadas,
            mensaje=Mensaje(Rol.ASISTENTE, texto, llamadas=llamadas, crudo=crudo),
            tokens_entrada=respuesta.usage.input_tokens,
            tokens_salida=respuesta.usage.output_tokens,
        )

    async def generar_json(
        self, mensajes: Sequence[Mensaje], esquema: dict[str, Any], opciones: Opciones
    ) -> dict[str, Any]:
        configuracion = {
            "effort": _ESFUERZO[opciones.esfuerzo],
            "format": {"type": "json_schema", "schema": adaptar_esquema(esquema, PROVEEDOR)},
        }
        respuesta = await self._crear("generar_json", mensajes, opciones, configuracion, {})
        return leer_objeto_json(_texto(respuesta))

    async def _crear(
        self,
        operacion: str,
        mensajes: Sequence[Mensaje],
        opciones: Opciones,
        configuracion: dict[str, Any],
        extra: dict[str, Any],
    ) -> Message:
        sistema = "\n\n".join(m.contenido for m in mensajes if m.rol is Rol.SISTEMA)
        if sistema:
            extra = {**extra, "system": sistema}
        inicio = time.monotonic()
        resultado = "error"  # cualquier fallo no previsto queda registrado como error
        respuesta: Message | None = None
        try:
            parametros: dict[str, Any] = {
                "model": self._modelo,
                "max_tokens": opciones.max_tokens,
                "messages": _mensajes(mensajes),
                "thinking": {"type": "between_tools"},
                "output_config": configuracion,
                "timeout": opciones.tiempo_maximo_s,
                **extra,
            }
            mensajes_api: Any = self._cliente.messages
            obtenida: Message = await asyncio.wait_for(
                mensajes_api.create(**parametros), timeout=opciones.tiempo_maximo_s
            )
            respuesta = obtenida
            resultado = "ok"
            return obtenida
        except (TimeoutError, anthropic.APITimeoutError) as error:
            resultado = "tiempo_agotado"
            raise LlmTiempoAgotado("El modelo no respondió a tiempo.") from error
        except anthropic.APIConnectionError as error:
            resultado = "sin_conexion"
            raise LlmNoDisponible("No se pudo conectar con la API de Anthropic.") from error
        except anthropic.APIStatusError as error:
            resultado = f"error_{error.status_code}"
            raise LlmNoDisponible(f"La API de Anthropic respondió {error.status_code}.") from error
        finally:
            registrar_llamada(
                proveedor=PROVEEDOR,
                modelo=self._modelo,
                operacion=operacion,
                duracion_ms=int((time.monotonic() - inicio) * 1000),
                resultado=resultado,
                opciones=opciones,
                tokens_entrada=None if respuesta is None else respuesta.usage.input_tokens,
                tokens_salida=None if respuesta is None else respuesta.usage.output_tokens,
            )


def _texto(respuesta: Message) -> str:
    return "".join(b.text for b in respuesta.content if b.type == "text")


def _mensajes(mensajes: Sequence[Mensaje]) -> list[dict[str, Any]]:
    """Traduce a la Messages API: los resultados de herramientas van como bloques `tool_result` del usuario, y los
    mensajes seguidos del mismo rol se juntan (la API exige que se alternen)."""
    salida: list[dict[str, Any]] = []
    for m in mensajes:
        if m.rol is Rol.SISTEMA:
            continue
        bloques: list[dict[str, Any]]
        if m.rol is Rol.ASISTENTE:
            rol = "assistant"
            bloques = list(m.crudo) if m.crudo else _bloques_asistente(m)
        elif m.rol is Rol.HERRAMIENTA:
            rol = "user"
            bloques = [
                {"type": "tool_result", "tool_use_id": m.id_llamada, "content": m.contenido, "is_error": m.es_error}
            ]
        else:
            rol = "user"
            bloques = [{"type": "text", "text": m.contenido}]
        if salida and salida[-1]["role"] == rol:
            salida[-1]["content"].extend(bloques)
        else:
            salida.append({"role": rol, "content": bloques})
    return salida


def _bloques_asistente(m: Mensaje) -> list[dict[str, Any]]:
    bloques: list[dict[str, Any]] = [{"type": "text", "text": m.contenido}] if m.contenido else []
    bloques += [{"type": "tool_use", "id": c.id, "name": c.nombre, "input": c.argumentos} for c in m.llamadas]
    return bloques
