"""Servidor MCP de IA (501): acceso a `/mcp` (401 / 403 / 200), hosts permitidos y `agente_ia` fuera del REST."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.tokens import ADMIN_ID, GERENTE_ID, encabezado
from protocolo_mcp.apoyo_mcp import (
    ENCABEZADOS_MCP,
    PETICION_LISTA,
    app_ia,
    cliente_mcp,
    encabezado_agente,
    http_mcp,
)

pytestmark = pytest.mark.integracion


@pytest.fixture
async def app(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    async with app_ia(motor, url_lector, ia_db, url_mongo_pruebas) as (aplicacion, http):
        yield aplicacion, http


async def _post_lista(app: FastAPI, encabezados: dict[str, str]) -> Any:
    async with http_mcp(app, {**ENCABEZADOS_MCP, **encabezados}) as http:
        return await http.post("/mcp", json=PETICION_LISTA)


async def test_sin_token_o_con_token_invalido_responde_401(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    for encabezados in ({}, {"Authorization": "Bearer x.y.z"}, encabezado_agente(otra_clave=True)):
        respuesta = await _post_lista(aplicacion, encabezados)
        assert respuesta.status_code == 401
        assert respuesta.headers["content-type"].startswith("application/problem+json")
        assert respuesta.json()["codigo"] == "NO_AUTENTICADO"


async def test_token_de_persona_responde_403(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    for encabezados in (encabezado(["gerente"], sub=GERENTE_ID), encabezado(["admin"], sub=ADMIN_ID)):
        respuesta = await _post_lista(aplicacion, encabezados)
        assert respuesta.status_code == 403
        assert respuesta.json()["codigo"] == "SIN_PERMISO"


async def test_agente_ia_de_otro_cliente_responde_403(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    """El rol `agente_ia` solo vale con `azp = ia-agentes` (la service account de ese cliente)."""
    aplicacion, _ = app
    for azp in ("erp", "gateway", None):
        respuesta = await _post_lista(
            aplicacion, encabezado(["agente_ia"], sub="a0000000-0000-4000-8000-0000000000a1", azp=azp)
        )
        assert respuesta.status_code == 403, azp
        assert respuesta.json()["codigo"] == "SIN_PERMISO"


async def test_agente_ia_responde_200_sin_redireccion(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    respuesta = await _post_lista(aplicacion, encabezado_agente())
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("application/json")
    assert "tools" in respuesta.json()["result"]


async def test_get_mcp_responde_405_sin_flujo_sse(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    async with http_mcp(aplicacion, {**ENCABEZADOS_MCP, **encabezado_agente()}) as http:
        respuesta = await http.get("/mcp")
    assert respuesta.status_code == 405


async def test_host_no_permitido_lo_rechaza_el_sdk(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    async with http_mcp(aplicacion, {**ENCABEZADOS_MCP, **encabezado_agente(), "host": "otro:9999"}) as http:
        respuesta = await http.post("/mcp", json=PETICION_LISTA)
    assert respuesta.status_code == 421


async def test_cliente_del_sdk_se_conecta_con_el_token_de_agente(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    async with cliente_mcp(aplicacion) as cliente:
        lista = await cliente.list_tools()
    assert isinstance(lista.tools, list)


@pytest.mark.parametrize(
    "ruta",
    ["/predicciones?obraIds=b0000000-0000-4000-8000-000000000001", "/consumos-atipicos", "/catalogo-reportes"],
)
async def test_agente_ia_no_sirve_en_las_rutas_rest(app: tuple[FastAPI, httpx.AsyncClient], ruta: str) -> None:
    _, http = app
    respuesta = await http.get(ruta, headers=encabezado_agente())
    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "SIN_PERMISO"
