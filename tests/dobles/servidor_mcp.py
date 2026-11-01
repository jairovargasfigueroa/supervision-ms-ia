"""Servidores MCP de prueba hechos con el mismo SDK (`mcp` 2.3, `MCPServer`), servidos por ASGI sin red.

Cada `ServidorPrueba` publica las herramientas que se le dan (con su esquema de entrada tal cual) y responde con una
función de Python. Registra los encabezados de cada petición y puede rechazar tokens con `401`.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, CallToolResult, TextContent, Tool, ToolAnnotations
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from ia.mcp.servidor.servidor import servidor_en_marcha

Respuesta = dict[str, Any] | Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass
class HerramientaPrueba:
    nombre: str
    esquema: dict[str, Any]
    respuesta: Respuesta = field(default_factory=dict)
    """Un `dict` fijo o una función de los argumentos (puede ser `async`)."""
    error: tuple[str, str] | None = None
    """(`codigo`, `mensaje`): responde `isError` con ese JSON."""
    descripcion: str = "Devuelve datos de prueba."


def esquema_objeto(propiedades: dict[str, Any], requeridas: list[str] | None = None) -> dict[str, Any]:
    esquema: dict[str, Any] = {"type": "object", "properties": propiedades, "additionalProperties": False}
    if requeridas:
        esquema["required"] = requeridas
    return esquema


class _ServidorFalso(MCPServer[Any]):
    def __init__(self, nombre: str, herramientas: list[HerramientaPrueba], llamadas: list[tuple[str, dict[str, Any]]]):
        super().__init__(nombre, subscriptions=False)
        self._herramientas = {h.nombre: h for h in herramientas}
        self._llamadas = llamadas

    async def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name=h.nombre,
                description=h.descripcion,
                input_schema=h.esquema,
                annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
            )
            for h in self._herramientas.values()
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult:
        herramienta = self._herramientas.get(name)
        if herramienta is None:
            raise MCPError(code=INVALID_PARAMS, message=f"Herramienta desconocida: {name}")
        self._llamadas.append((name, dict(arguments)))
        if herramienta.error is not None:
            codigo, mensaje = herramienta.error
            texto = json.dumps({"codigo": codigo, "mensaje": mensaje}, ensure_ascii=False)
            return CallToolResult(content=[TextContent(type="text", text=texto)], is_error=True)
        datos = herramienta.respuesta
        if callable(datos):
            datos = datos(arguments)
            if asyncio.iscoroutine(datos):
                datos = await datos
        assert isinstance(datos, dict)
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(datos, ensure_ascii=False))], structured_content=datos
        )


class ServidorPrueba:
    def __init__(self, nombre: str, herramientas: list[HerramientaPrueba]) -> None:
        self.nombre = nombre
        self.llamadas: list[tuple[str, dict[str, Any]]] = []
        self.encabezados: list[dict[str, str]] = []
        self.tokens_rechazados: set[str] = set()
        self.caido = False
        self.demora_s = 0.0
        self._mcp = _ServidorFalso(nombre, herramientas, self.llamadas)
        self._app = self._mcp.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )

    @property
    def url(self) -> str:
        return f"http://{self.nombre}.prueba/mcp"

    def transporte(self) -> httpx2.AsyncBaseTransport:
        return httpx2.ASGITransport(app=self._asgi)

    def herramientas_llamadas(self) -> list[str]:
        return [nombre for nombre, _ in self.llamadas]

    async def _asgi(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        if self.caido:
            raise OSError("servidor caído")
        encabezados = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        self.encabezados.append(encabezados)
        if self.demora_s:
            await asyncio.sleep(self.demora_s)
        token = encabezados.get("authorization", "").removeprefix("Bearer ")
        if token in self.tokens_rechazados:
            await Response(status_code=401)(scope, receive, send)
            return
        app: ASGIApp = self._app
        await app(scope, receive, send)

    @asynccontextmanager
    async def en_marcha(self) -> AsyncIterator["ServidorPrueba"]:
        async with servidor_en_marcha(self._mcp):
            yield self


@asynccontextmanager
async def servidores_en_marcha(*servidores: ServidorPrueba) -> AsyncIterator[None]:
    async with AsyncExitStack() as pila:
        for servidor in servidores:
            await pila.enter_async_context(servidor.en_marcha())
        yield
