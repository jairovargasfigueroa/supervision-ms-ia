"""Apoyo de las pruebas del servidor MCP de IA (501): la API real (ASGI) con PostgreSQL y MongoDB reales y un cliente
MCP del SDK `mcp` que habla HTTP «streamable» por `httpx2.ASGITransport` (sin red)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import httpx2
from fastapi import FastAPI
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apoyo import reportes_503 as d
from apoyo.tokens import encabezado, validador
from constructor_reportes.apoyo_api import MaterialesSimulados
from dobles.modelo_simulado import ModeloSimulado
from ia.comun.tiempo import RelojFijo
from ia.config import Configuracion
from ia.main import crear_app

BASE = "http://localhost:8085"
AGENTE_ID = "a0000000-0000-4000-8000-0000000000a1"


def encabezado_agente(**opciones: Any) -> dict[str, str]:
    """Token de la *service account* de `ia-agentes` (rol de realm `agente_ia`)."""
    return encabezado(["agente_ia"], sub=AGENTE_ID, azp="ia-agentes", **opciones)


@asynccontextmanager
async def app_ia(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo: str
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """La app con su *lifespan* en marcha y un cliente REST (`httpx`) para comparar cifras."""
    config = Configuracion(mongo_url=url_mongo, mongo_db=ia_db.name)
    lector = create_async_engine(url_lector, pool_size=2)
    app = crear_app(
        config,
        validador=validador(),
        motor=motor,
        motor_lector=lector,
        cargar_modelo_riesgo=False,
        modelo_lenguaje=ModeloSimulado(),
        catalogo_materiales=MaterialesSimulados(),
    )
    app.state.reloj = RelojFijo(d.AHORA)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as http,
        ):
            yield app, http
    finally:
        await lector.dispose()


def http_mcp(app: FastAPI, encabezados: dict[str, str] | None = None) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=BASE, headers=encabezados or {}, timeout=10
    )


@asynccontextmanager
async def cliente_mcp(app: FastAPI, encabezados: dict[str, str] | None = None) -> AsyncIterator[Client]:
    """Cliente MCP del SDK con el token de `agente_ia` (o los encabezados dados)."""
    http = http_mcp(app, encabezado_agente() if encabezados is None else encabezados)
    async with Client(streamable_http_client(f"{BASE}/mcp", http_client=http)) as cliente:
        yield cliente


PETICION_LISTA = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
ENCABEZADOS_MCP = {"accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-11-25"}
