"""`ClienteMcp`: el host (`ia-worker`) habla con un servidor MCP (501) con el cliente oficial `Client` del SDK `mcp`.

- Encabezados `Authorization: Bearer <token de ia-agentes>` y `X-Request-Id: <ejecucionId>` (un `httpx2.AsyncClient`
  propio, que es el cliente HTTP del SDK).
- Versión de protocolo en modo `auto`: prueba la 2026-07-28 y, si el servidor es anterior, cae a `initialize`.
- Cada operación abre su conexión y la cierra en la misma tarea (los servidores son sin estado), con 10 s como máximo.
- Si el servidor responde `401` (token vencido o revocado), se descarta el token y se reintenta una vez.
- Lo que falle (servidor caído, lento o error del protocolo) es `FalloMcp`: el agente lo registra en el paso y sigue.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import CallToolResult, TextContent

from ia.mcp.cliente.token import TokenAgentes, TokenNoDisponible

registro = logging.getLogger("ia.mcp.cliente")

TIEMPO_MAXIMO_S = 10.0


class FalloMcp(Exception):
    """La llamada no se pudo hacer: servidor caído, lento (más de 10 s), sin token o error del protocolo."""

    def __init__(self, mensaje: str, *, tiempo_agotado: bool = False) -> None:
        super().__init__(mensaje)
        self.tiempo_agotado = tiempo_agotado


@dataclass(frozen=True)
class HerramientaPublicada:
    nombre: str
    descripcion: str
    esquema_entrada: dict[str, Any]
    esquema_salida: dict[str, Any] | None = None
    solo_lectura: bool = False


@dataclass(frozen=True)
class ResultadoHerramienta:
    es_error: bool
    """`isError` del resultado (un error de negocio de la herramienta: vuelve al modelo como dato)."""
    datos: Any
    """`structuredContent` o, si no viene, el JSON del texto (o el texto tal cual)."""
    texto: str
    codigo: str | None = None
    """Con `es_error`: el `codigo` del JSON `{codigo, mensaje}` (`ARGUMENTO_INVALIDO`, `OBRA_NO_ENCONTRADA`…)."""
    mensaje: str | None = None


@dataclass
class _Estado:
    no_autorizado: bool = field(default=False)


class ClienteMcp:
    def __init__(
        self,
        servidor: str,
        url: str,
        token: TokenAgentes,
        request_id: str,
        *,
        tiempo_maximo_s: float = TIEMPO_MAXIMO_S,
        transporte: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self.servidor = servidor
        self.url = url
        self._token = token
        self._request_id = request_id
        self._tiempo_maximo_s = tiempo_maximo_s
        self._transporte = transporte

    async def listar_herramientas(self) -> list[HerramientaPublicada]:
        async def operar(cliente: Client) -> list[HerramientaPublicada]:
            lista = await cliente.list_tools()
            return [
                HerramientaPublicada(
                    nombre=h.name,
                    descripcion=h.description or "",
                    esquema_entrada=dict(h.input_schema),
                    esquema_salida=None if h.output_schema is None else dict(h.output_schema),
                    solo_lectura=bool(h.annotations and h.annotations.read_only_hint),
                )
                for h in lista.tools
            ]

        return await self._con_reintento("tools/list", operar)

    async def llamar(self, nombre: str, argumentos: dict[str, Any]) -> ResultadoHerramienta:
        async def operar(cliente: Client) -> ResultadoHerramienta:
            return _leer_resultado(await cliente.call_tool(nombre, argumentos))

        return await self._con_reintento(f"tools/call {nombre}", operar)

    async def _con_reintento[T](self, operacion: str, operar: Callable[[Client], Awaitable[T]]) -> T:
        for intento in (1, 2):
            estado = _Estado()
            try:
                async with asyncio.timeout(self._tiempo_maximo_s), self._conectar(estado) as cliente:
                    return await operar(cliente)
            except TokenNoDisponible as error:
                raise FalloMcp(f"Sin token de ia-agentes: {error}") from error
            except TimeoutError as error:
                registro.warning("El servidor MCP %s no respondió a %s a tiempo.", self.servidor, operacion)
                raise FalloMcp(
                    f"El servidor {self.servidor} no respondió en {self._tiempo_maximo_s:g} s.", tiempo_agotado=True
                ) from error
            except Exception as error:
                if estado.no_autorizado:
                    if intento == 1:
                        self._token.invalidar()
                        continue
                    break
                protocolo = _buscar(error, MCPError)
                if protocolo is not None:
                    raise FalloMcp(f"Error del protocolo MCP en {self.servidor}: {protocolo}") from error
                registro.warning("El servidor MCP %s falló en %s: %s", self.servidor, operacion, type(error).__name__)
                raise FalloMcp(f"El servidor {self.servidor} no está disponible.") from error
        raise FalloMcp(f"El servidor {self.servidor} rechazó el token de ia-agentes.")

    @asynccontextmanager
    async def _conectar(self, estado: _Estado) -> AsyncIterator[Client]:
        token = await self._token.token()

        async def al_responder(respuesta: httpx2.Response) -> None:
            if respuesta.status_code == 401:
                estado.no_autorizado = True

        http = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}", "X-Request-Id": self._request_id},
            timeout=self._tiempo_maximo_s,
            transport=self._transporte,
            event_hooks={"response": [al_responder]},
        )
        # El SDK no cierra un cliente HTTP que se le pasa: se cierra aquí, al terminar la operación.
        async with (
            http,
            Client(streamable_http_client(self.url, http_client=http), mode="auto", cache=None) as cliente,
        ):
            yield cliente


def _buscar[E: BaseException](error: BaseException, tipo: type[E]) -> E | None:
    """El error de `tipo`, aunque venga dentro de un grupo de excepciones (los grupos de tareas de anyio)."""
    if isinstance(error, tipo):
        return error
    if isinstance(error, BaseExceptionGroup):
        for interno in error.exceptions:
            encontrado = _buscar(interno, tipo)
            if encontrado is not None:
                return encontrado
    if error.__cause__ is not None and error.__cause__ is not error:
        return _buscar(error.__cause__, tipo)
    return None


def _leer_resultado(resultado: CallToolResult) -> ResultadoHerramienta:
    texto = "".join(c.text for c in resultado.content if isinstance(c, TextContent))
    datos: Any = resultado.structured_content
    if datos is None:
        try:
            datos = json.loads(texto)
        except ValueError:
            datos = texto
    if not resultado.is_error:
        return ResultadoHerramienta(False, datos, texto)
    codigo = datos.get("codigo") if isinstance(datos, dict) else None
    mensaje = datos.get("mensaje") if isinstance(datos, dict) else texto
    return ResultadoHerramienta(
        True, datos, texto, codigo if isinstance(codigo, str) else None, mensaje if isinstance(mensaje, str) else None
    )
