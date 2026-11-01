"""Adaptador de Ollama (cliente oficial `ollama` 0.6, `AsyncClient`).

`chat(model, messages, tools=…, format=<JSON Schema>, think=False, options={temperature, num_ctx: 16384})`.
`num_ctx` 16384 porque el catálogo y los resultados de herramientas no entran en 8192 (decisión del equipo).
Ollama no da id a las llamadas a herramientas: el adaptador les pone `h1`, `h2`… (únicos en la conversación).
"""

import asyncio
import json
import time
from collections.abc import Sequence
from typing import Any

import httpx
from ollama import AsyncClient, ChatResponse, ResponseError

from ia.lenguaje.aplicacion.puerto import LlmNoDisponible, LlmTiempoAgotado, SalidaNoValida, registrar_llamada
from ia.lenguaje.dominio.mensajes import (
    DefinicionHerramienta,
    LlamadaHerramienta,
    Mensaje,
    Opciones,
    RespuestaModelo,
    Rol,
)
from ia.lenguaje.infraestructura.esquemas import adaptar_esquema

PROVEEDOR = "ollama"
NUM_CTX = 16384

_ROLES = {Rol.SISTEMA: "system", Rol.USUARIO: "user", Rol.ASISTENTE: "assistant", Rol.HERRAMIENTA: "tool"}


class ModeloOllama:
    def __init__(self, url: str, modelo: str, transporte: httpx.AsyncBaseTransport | None = None) -> None:
        """`transporte`: solo en las pruebas (transporte HTTP simulado de `httpx`)."""
        self._modelo = modelo
        extra: dict[str, Any] = {} if transporte is None else {"transport": transporte}
        self._cliente = AsyncClient(host=url, **extra)

    @property
    def proveedor(self) -> str:
        return PROVEEDOR

    @property
    def modelo(self) -> str:
        return self._modelo

    async def conversar(
        self, mensajes: Sequence[Mensaje], herramientas: Sequence[DefinicionHerramienta], opciones: Opciones
    ) -> RespuestaModelo:
        herramientas_ollama = [
            {
                "type": "function",
                "function": {
                    "name": h.nombre,
                    "description": h.descripcion,
                    "parameters": adaptar_esquema(h.esquema, PROVEEDOR),
                },
            }
            for h in herramientas
        ]
        respuesta = await self._chat("conversar", mensajes, opciones, tools=herramientas_ollama or None)
        previas = sum(len(m.llamadas) for m in mensajes)
        llamadas = tuple(
            LlamadaHerramienta(id=f"h{previas + i}", nombre=c.function.name, argumentos=dict(c.function.arguments))
            for i, c in enumerate(respuesta.message.tool_calls or [], start=1)
        )
        texto = respuesta.message.content or ""
        return RespuestaModelo(
            texto=texto,
            llamadas=llamadas,
            mensaje=Mensaje(Rol.ASISTENTE, texto, llamadas=llamadas),
            tokens_entrada=respuesta.prompt_eval_count,
            tokens_salida=respuesta.eval_count,
        )

    async def generar_json(
        self, mensajes: Sequence[Mensaje], esquema: dict[str, Any], opciones: Opciones
    ) -> dict[str, Any]:
        respuesta = await self._chat("generar_json", mensajes, opciones, format=adaptar_esquema(esquema, PROVEEDOR))
        return leer_objeto_json(respuesta.message.content or "")

    async def _chat(
        self, operacion: str, mensajes: Sequence[Mensaje], opciones: Opciones, **extra: Any
    ) -> ChatResponse:
        opciones_ollama: dict[str, Any] = {"num_ctx": NUM_CTX}
        if opciones.temperatura is not None:
            opciones_ollama["temperature"] = opciones.temperatura
        inicio = time.monotonic()
        resultado = "ok"
        respuesta: ChatResponse | None = None
        try:
            obtenida: ChatResponse = await asyncio.wait_for(
                self._cliente.chat(
                    model=self._modelo,
                    messages=[_mensaje(m) for m in mensajes],
                    think=False,
                    options=opciones_ollama,
                    **extra,
                ),
                timeout=opciones.tiempo_maximo_s,
            )
            respuesta = obtenida
            return obtenida
        except (TimeoutError, httpx.TimeoutException) as error:
            resultado = "tiempo_agotado"
            raise LlmTiempoAgotado("El modelo no respondió a tiempo.") from error
        except ResponseError as error:
            resultado = f"error_{error.status_code}"
            raise LlmNoDisponible(f"Ollama respondió {error.status_code}.") from error
        except (ConnectionError, httpx.HTTPError) as error:
            resultado = "sin_conexion"
            raise LlmNoDisponible("No se pudo conectar con Ollama.") from error
        finally:
            registrar_llamada(
                proveedor=PROVEEDOR,
                modelo=self._modelo,
                operacion=operacion,
                duracion_ms=int((time.monotonic() - inicio) * 1000),
                resultado=resultado,
                opciones=opciones,
                tokens_entrada=None if respuesta is None else respuesta.prompt_eval_count,
                tokens_salida=None if respuesta is None else respuesta.eval_count,
            )


def leer_objeto_json(texto: str) -> dict[str, Any]:
    """`SalidaNoValida` si no es JSON o no es un objeto."""
    try:
        datos = json.loads(texto)
    except json.JSONDecodeError as error:
        raise SalidaNoValida("La respuesta del modelo no es JSON.") from error
    if not isinstance(datos, dict):
        raise SalidaNoValida("La respuesta del modelo no es un objeto JSON.")
    return datos


def _mensaje(m: Mensaje) -> dict[str, Any]:
    datos: dict[str, Any] = {"role": _ROLES[m.rol], "content": m.contenido}
    if m.llamadas:
        datos["tool_calls"] = [{"function": {"name": c.nombre, "arguments": c.argumentos}} for c in m.llamadas]
    if m.rol is Rol.HERRAMIENTA and m.nombre_herramienta:
        datos["tool_name"] = m.nombre_herramienta
    return datos
