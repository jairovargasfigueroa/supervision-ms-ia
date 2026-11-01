"""Contrato del servidor MCP de IA (501): `tools/list` exacto, herramienta desconocida, registro de cada llamada y
prueba de «solo lectura» (ninguna tabla ni colección cambia después de llamar todas las herramientas)."""

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp.shared.exceptions import MCPError
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo import reportes_503 as d
from apoyo.base_datos import TABLAS
from protocolo_mcp.apoyo_mcp import app_ia, cliente_mcp, encabezado_agente
from protocolo_mcp.test_herramientas_ia import guardar_predicciones, llamar

pytestmark = pytest.mark.integracion

HERRAMIENTAS = {"consultar_reporte", "riesgo_retraso", "consumos_atipicos"}
ENTRADAS = {
    "consultar_reporte": {"conjunto", "columnas", "filtros", "agruparPor", "metricas", "orden", "limite"},
    "riesgo_retraso": {"obraIds", "niveles", "limite"},
    "consumos_atipicos": {"obraId", "estados", "dias", "limite"},
}


@pytest.fixture
async def app(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    await d.cargar(motor, ia_db)
    await guardar_predicciones(ia_db)
    async with app_ia(motor, url_lector, ia_db, url_mongo_pruebas) as (aplicacion, http):
        yield aplicacion, http


async def test_tools_list_es_exactamente_el_contrato(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    async with cliente_mcp(aplicacion) as cliente:
        lista = await cliente.list_tools()
        assert (await cliente.list_resources()).resources == []
        assert (await cliente.list_resource_templates()).resource_templates == []
        assert (await cliente.list_prompts()).prompts == []
    assert {h.name for h in lista.tools} == HERRAMIENTAS
    for herramienta in lista.tools:
        assert herramienta.description is not None
        assert herramienta.description.startswith("Devuelve")
        assert herramienta.annotations is not None
        assert herramienta.annotations.read_only_hint is True
        assert herramienta.annotations.open_world_hint is False
        entrada = herramienta.input_schema
        assert entrada["type"] == "object"
        assert entrada["additionalProperties"] is False
        assert set(entrada["properties"]) == ENTRADAS[herramienta.name]
        assert "description" not in entrada  # sin textos del código para el modelo
        assert herramienta.output_schema is not None
        assert herramienta.output_schema["additionalProperties"] is False
    receta = next(h for h in lista.tools if h.name == "consultar_reporte").input_schema
    assert "NINGUNO" not in json.dumps(receta["properties"]["conjunto"])
    assert receta["required"] == ["conjunto"]


async def test_herramienta_desconocida_es_error_del_protocolo(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    async with cliente_mcp(aplicacion) as cliente:
        for nombre in ("borrar_obra", "revisar_consumo_atipico", "asistencia_obra"):
            with pytest.raises(MCPError, match="desconocida"):
                await cliente.call_tool(nombre, {})


async def test_cada_llamada_queda_registrada_sin_el_token(
    app: tuple[FastAPI, httpx.AsyncClient], caplog: pytest.LogCaptureFixture
) -> None:
    aplicacion, _ = app
    encabezados = {**encabezado_agente(), "X-Request-Id": "ejecucion-123"}
    with caplog.at_level(logging.INFO, logger="ia.mcp.servidor"):
        async with cliente_mcp(aplicacion, encabezados) as cliente:
            await llamar(cliente, "consumos_atipicos", {"limite": 1})
            await llamar(cliente, "riesgo_retraso", {"limite": 1000})
    lineas = [json.loads(r.getMessage().split(": ", 1)[1]) for r in caplog.records if r.name == "ia.mcp.servidor"]
    assert [(x["herramienta"], x["filas"], x["requestId"], x["error"]) for x in lineas] == [
        ("consumos_atipicos", 1, "ejecucion-123", None),
        ("riesgo_retraso", 0, "ejecucion-123", "ARGUMENTO_INVALIDO"),
    ]
    assert all(isinstance(x["duracionMs"], int) for x in lineas)
    assert '"limite": 1' in lineas[0]["argumentos"]
    token = encabezados["Authorization"].split(" ")[1]
    assert all(token not in r.getMessage() and "Bearer" not in r.getMessage() for r in caplog.records)


async def _huella(motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]]) -> dict[str, str]:
    huellas: dict[str, str] = {}
    async with motor.connect() as conexion:
        for tabla in TABLAS:
            filas = (await conexion.execute(text(f"SELECT t::text FROM {tabla} t ORDER BY 1"))).scalars().all()
            huellas[tabla] = f"{len(filas)}:{hashlib.sha256('|'.join(filas).encode()).hexdigest()}"
    for coleccion in sorted(await ia_db.list_collection_names()):
        documentos = [repr(sorted(doc.items())) async for doc in ia_db[coleccion].find().sort("_id", 1)]
        huellas[coleccion] = f"{len(documentos)}:{hashlib.sha256('|'.join(documentos).encode()).hexdigest()}"
    return huellas


async def test_solo_lectura_ninguna_tabla_ni_coleccion_cambia(
    app: tuple[FastAPI, httpx.AsyncClient], motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]]
) -> None:
    aplicacion, _ = app
    antes = await _huella(motor, ia_db)
    async with cliente_mcp(aplicacion) as cliente:
        await llamar(cliente, "riesgo_retraso", {})
        await llamar(cliente, "riesgo_retraso", {"obraIds": [d.O1], "niveles": ["ALTO", "SIN_DATOS"]})
        await llamar(cliente, "consumos_atipicos", {"estados": ["PENDIENTE", "NORMAL", "CONFIRMADO"], "dias": 90})
        for conjunto in ("OBRAS", "CONSUMO_MATERIAL", "MOVIMIENTOS", "COBROS", "PLANILLAS", "CONSUMOS_ATIPICOS"):
            error, _ = await llamar(cliente, "consultar_reporte", {"conjunto": conjunto})
            assert not error, conjunto
    assert await _huella(motor, ia_db) == antes
    assert "consumo_atipico" in antes
    assert "prediccion" in antes
