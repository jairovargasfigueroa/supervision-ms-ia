"""Servidor MCP de IA (501) con el SDK oficial `mcp` 2.3 (`MCPServer`).

`ServidorMcp` sobrescribe `list_tools` y `call_tool` (métodos públicos de `MCPServer`) para que cada herramienta
publique esquemas estrictos de Pydantic (`additionalProperties: false`) y para devolver los errores de negocio como
resultado (`isError: true` con `{"codigo", "mensaje"}`). Una herramienta que no existe es un error del protocolo.

`montar_mcp` lo publica en la ruta exacta `/mcp` de `ia-api`, sin estado y con respuestas JSON, detrás de
`SeguridadMcp` (solo `agente_ia`). El *lifespan* de la app entra en `session_manager.run()`.
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import BaseModel, ValidationError
from starlette.routing import Route

from ia.comun.registros import id_peticion
from ia.mcp.servidor.seguridad import SeguridadMcp

registro = logging.getLogger("ia.mcp.servidor")

ARGUMENTO_INVALIDO = "ARGUMENTO_INVALIDO"
OBRA_NO_ENCONTRADA = "OBRA_NO_ENCONTRADA"
ERROR_INTERNO = "ERROR_INTERNO"

TIEMPO_MAXIMO_S = 5.0
"""Tiempo máximo de una herramienta (consulta incluida)."""
PROBLEMAS_MAXIMOS = 5
SOLO_LECTURA = ToolAnnotations(read_only_hint=True, open_world_hint=False)


class ErrorHerramienta(Exception):
    """Error de negocio de una herramienta: vuelve como resultado con `isError: true`."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


@dataclass(frozen=True)
class Herramienta:
    nombre: str
    descripcion: str
    entrada: type[BaseModel]
    """Modelo con `extra="forbid"`: de él sale el `inputSchema` y con él se validan los argumentos."""
    salida: type[BaseModel]
    """De él sale el `outputSchema` y el `structuredContent`."""
    ejecutar: Callable[[Any], Awaitable[BaseModel]]
    """Recibe la entrada ya validada; lanza `ErrorHerramienta` para los errores de negocio."""
    filas: Callable[[Any], int]
    """Filas devueltas (para el registro)."""

    def definicion(self) -> Tool:
        return Tool(
            name=self.nombre,
            description=self.descripcion,
            input_schema=esquema(self.entrada, "validation"),
            output_schema=esquema(self.salida, "serialization"),
            annotations=SOLO_LECTURA,
        )


def esquema(modelo: type[BaseModel], modo: str) -> dict[str, Any]:
    """JSON Schema del modelo con los nombres en camelCase (por alias)."""
    return modelo.model_json_schema(by_alias=True, mode="validation" if modo == "validation" else "serialization")


def problemas(error: ValidationError) -> list[str]:
    """Hasta 5 problemas con su ruta (sin repetir los valores recibidos, que son datos del que llama)."""
    salida = []
    for detalle in error.errors()[:PROBLEMAS_MAXIMOS]:
        ruta = ".".join(str(p) for p in detalle.get("loc", ())) or "argumentos"
        salida.append(f"{ruta}: {detalle.get('msg', '')}")
    return salida


def resultado_error(codigo: str, mensaje: str) -> CallToolResult:
    texto = json.dumps({"codigo": codigo, "mensaje": mensaje}, ensure_ascii=False)
    return CallToolResult(content=[TextContent(type="text", text=texto)], is_error=True)


class ServidorMcp(MCPServer[Any]):
    def __init__(self, herramientas: Sequence[Herramienta]) -> None:
        # Sin recursos ni *prompts* (las listas quedan vacías) y sin `subscriptions/listen`: solo herramientas.
        super().__init__("ia", version="0.1.0", subscriptions=False)
        self._herramientas: dict[str, Herramienta] = {h.nombre: h for h in herramientas}

    @property
    def herramientas(self) -> Mapping[str, Herramienta]:
        return self._herramientas

    async def list_tools(self) -> list[Tool]:
        return [h.definicion() for h in self._herramientas.values()]

    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context[Any, Any] | None = None
    ) -> CallToolResult:
        herramienta = self._herramientas.get(name)
        if herramienta is None:
            raise MCPError(code=INVALID_PARAMS, message=f"Herramienta desconocida: {name}")
        request_id = _request_id(context)
        marca = id_peticion.set(request_id)
        inicio = time.perf_counter()
        filas = 0
        codigo: str | None = None
        try:
            try:
                entrada = herramienta.entrada.model_validate(arguments)
            except ValidationError as error:
                codigo = ARGUMENTO_INVALIDO
                return resultado_error(codigo, "Argumentos inválidos: " + "; ".join(problemas(error)))
            try:
                async with asyncio.timeout(TIEMPO_MAXIMO_S):
                    salida = await herramienta.ejecutar(entrada)
            except ErrorHerramienta as error:
                codigo = error.codigo
                return resultado_error(error.codigo, error.mensaje)
            except Exception:
                registro.exception("La herramienta MCP %s falló.", name)
                codigo = ERROR_INTERNO
                return resultado_error(codigo, "No se pudo consultar los datos. Intenta de nuevo en un momento.")
            filas = herramienta.filas(salida)
            datos = salida.model_dump(by_alias=True, mode="json")
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(datos, ensure_ascii=False))],
                structured_content=datos,
            )
        finally:
            _registrar(name, arguments, filas, inicio, request_id, codigo)
            id_peticion.reset(marca)


def _request_id(context: Context[Any, Any] | None) -> str | None:
    if context is None:
        return None
    try:
        encabezados = context.headers
    except (AttributeError, LookupError, ValueError):
        return None
    return None if encabezados is None else encabezados.get("x-request-id")


def _registrar(
    herramienta: str, argumentos: Any, filas: int, inicio: float, request_id: str | None, codigo: str | None
) -> None:
    """Una línea JSON por llamada (sin encabezados: nunca el token). Los argumentos se recortan."""
    texto_argumentos = json.dumps(argumentos, ensure_ascii=False, default=str)[:1000]
    registro.info(
        "Herramienta MCP: %s",
        json.dumps(
            {
                "herramienta": herramienta,
                "argumentos": texto_argumentos,
                "filas": filas,
                "duracionMs": int((time.perf_counter() - inicio) * 1000),
                "requestId": request_id,
                "error": codigo,
            },
            ensure_ascii=False,
        ),
    )


def montar_mcp(app: FastAPI, servidor: ServidorMcp, hosts_permitidos: Sequence[str]) -> None:
    """Ruta exacta `/mcp` (sin la redirección a `/mcp/` de `app.mount`) detrás de `SeguridadMcp`."""
    aplicacion = servidor.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=list(hosts_permitidos)
        ),
    )
    app.router.routes.append(Route("/mcp", endpoint=SeguridadMcp(aplicacion)))


@asynccontextmanager
async def servidor_en_marcha(servidor: MCPServer[Any]) -> AsyncIterator[None]:
    """`session_manager.run()` en una tarea propia: su grupo de tareas (anyio) se abre y se cierra en la misma tarea
    aunque el *lifespan* de la app se recorra desde tareas distintas (por ejemplo, las fixtures de pytest)."""
    listo = asyncio.Event()
    terminar = asyncio.Event()

    async def correr() -> None:
        async with servidor.session_manager.run():
            listo.set()
            await terminar.wait()

    tarea = asyncio.create_task(correr())
    esperar_listo = asyncio.create_task(listo.wait())
    await asyncio.wait({tarea, esperar_listo}, return_when=asyncio.FIRST_COMPLETED)
    if tarea.done():
        esperar_listo.cancel()
        tarea.result()  # propaga el error de arranque
    try:
        yield
    finally:
        terminar.set()
        await tarea
