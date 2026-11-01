"""Herramienta `consultar_reporte` (501): el mismo motor y la misma validación que `POST /consultas-reporte` (503),
en modo `MCP`."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apoyo import reportes_503 as d
from apoyo.tokens import GERENTE_ID, encabezado
from constructor_reportes.apoyo_api import DIRECTORIO
from dobles.modelo_simulado import ModeloSimulado
from protocolo_mcp.apoyo_mcp import app_ia, cliente_mcp
from protocolo_mcp.test_herramientas_ia import llamar

pytestmark = pytest.mark.integracion

GERENTE = encabezado(["gerente"], sub=GERENTE_ID)

RECETAS: list[dict[str, Any]] = [
    {"conjunto": "OBRAS", "orden": [{"por": "obra", "direccion": "ASC"}]},
    {"conjunto": "CONSUMO_MATERIAL", "agruparPor": ["obra"], "metricas": [{"funcion": "SUMA", "campo": "cantidad"}]},
    {"conjunto": "MOVIMIENTOS", "agruparPor": ["obra", "tipo"]},
    {"conjunto": "COBROS", "columnas": ["numero", "obra", "monto", "estado"], "orden": [{"por": "numero"}]},
    {"conjunto": "PLANILLAS", "agruparPor": ["obra", "estado"]},
    {"conjunto": "CONSUMOS_ATIPICOS", "agruparPor": ["obra", "estado_revision"]},
]


@pytest.fixture
async def app(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    await d.cargar(motor, ia_db)
    async with app_ia(motor, url_lector, ia_db, url_mongo_pruebas) as (aplicacion, http):
        yield aplicacion, http


@pytest.mark.parametrize("receta", RECETAS, ids=[r["conjunto"] for r in RECETAS])
async def test_mismas_cifras_que_el_rest(app: tuple[FastAPI, httpx.AsyncClient], receta: dict[str, Any]) -> None:
    aplicacion, http = app
    receta = {**receta, "limite": 50}
    aplicacion.state.modelo_lenguaje = ModeloSimulado(json_=[receta])
    rest = await http.post(
        "/consultas-reporte", json={"texto": "reporte de prueba", "directorio": DIRECTORIO}, headers=GERENTE
    )
    assert rest.status_code == 200, rest.text
    async with cliente_mcp(aplicacion) as cliente:
        error, salida = await llamar(cliente, "consultar_reporte", receta)
    assert not error, salida
    esperado = rest.json()
    assert salida["conjunto"] == esperado["conjunto"]
    assert salida["titulo"] == esperado["tituloConjunto"]
    assert [c["clave"] for c in salida["columnas"]] == [c["clave"] for c in esperado["columnas"]]
    assert salida["filas"] == esperado["filas"]
    assert (salida["totalFilas"], salida["truncado"]) == (esperado["totalFilas"], esperado["truncado"])
    assert salida["totalFilas"] > 0


async def test_modo_mcp_filtra_por_id_y_devuelve_ids(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    receta = {
        "conjunto": "CONSUMO_MATERIAL",
        "filtros": [{"campo": "obraId", "operador": "IGUAL", "valor": d.O1}],
        "agruparPor": ["material"],
    }
    async with cliente_mcp(aplicacion) as cliente:
        error, salida = await llamar(cliente, "consultar_reporte", receta)
    assert not error, salida
    assert salida["columnas"][0]["clave"] == "material"  # sin la columna «Unidad» del modo REST
    assert {f[0] for f in salida["filas"]} <= {d.CEMENTO, d.FIERRO}
    assert salida["aplicado"]


async def test_limite_por_defecto_20_y_truncado(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    async with cliente_mcp(aplicacion) as cliente:
        _, uno = await llamar(cliente, "consultar_reporte", {"conjunto": "MOVIMIENTOS", "limite": 1})
    assert (uno["totalFilas"], uno["truncado"]) == (1, True)


def _filtro(campo: str, operador: str, valor: Any) -> list[dict[str, Any]]:
    return [{"campo": campo, "operador": operador, "valor": valor}]


RECETAS_INVALIDAS: list[tuple[dict[str, Any], str]] = [
    ({"conjunto": "CONSUMO_MATERIAL", "filtros": _filtro("material", "CONTIENE", "Cem")}, "filtros[0].campo"),
    ({"conjunto": "COBROS", "filtros": _filtro("cliente", "CONTIENE", "Andina")}, "filtros[0].campo"),
    ({"conjunto": "OBRAS", "orden": [{"por": "nombre; DROP TABLE rpt_obra_resumen"}]}, "orden[0].por"),
    ({"conjunto": "PLANILLAS", "agruparPor": ["ultimo_motivo"]}, "agruparPor"),
    ({"conjunto": "COBROS", "limite": 51}, "limite"),
    ({"conjunto": "NINGUNO"}, "conjunto"),
    ({"conjunto": "USUARIOS"}, "conjunto"),
    ({"conjunto": "COBROS", "sql": "SELECT 1"}, "sql"),
    ({"conjunto": "COBROS", "agrupar_por": ["obra"]}, "agrupar_por"),
    ({"conjunto": "CONSUMOS_ATIPICOS", "filtros": _filtro("estadoRevision", "IGUAL", {"$ne": None})}, "valor"),
]


@pytest.mark.parametrize(("receta", "ruta"), RECETAS_INVALIDAS)
async def test_receta_invalida_es_argumento_invalido_con_ruta(
    app: tuple[FastAPI, httpx.AsyncClient], receta: dict[str, Any], ruta: str
) -> None:
    aplicacion, _ = app
    async with cliente_mcp(aplicacion) as cliente:
        error, salida = await llamar(cliente, "consultar_reporte", receta)
    assert error
    assert salida["codigo"] == "ARGUMENTO_INVALIDO"
    assert ruta in salida["mensaje"]


async def test_base_de_reportes_caida_es_error_interno(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> None:
    async with app_ia(motor, url_lector, ia_db, url_mongo_pruebas) as (aplicacion, _):
        caida = create_async_engine("postgresql+psycopg://nadie:nadie@127.0.0.1:1/ninguna")
        aplicacion.state.motor_lector = caida
        try:
            async with cliente_mcp(aplicacion) as cliente:
                error, salida = await llamar(cliente, "consultar_reporte", {"conjunto": "COBROS"})
        finally:
            await caida.dispose()
    assert error
    assert salida["codigo"] == "ERROR_INTERNO"
