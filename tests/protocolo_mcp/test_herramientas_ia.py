"""Herramientas `riesgo_retraso` (402) y `consumos_atipicos` (403) del servidor MCP de IA (501): cifras iguales al
REST, filtros, `truncado`, obra inexistente y argumentos inválidos."""

import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from mcp import Client
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo import reportes_503 as d
from apoyo.tokens import GERENTE_ID, encabezado
from ia.riesgo.aplicacion.puertos import Prediccion
from ia.riesgo.dominio.variables import Factor
from ia.riesgo.infraestructura.predicciones_mongo import AlmacenPrediccionesMongo
from protocolo_mcp.apoyo_mcp import app_ia, cliente_mcp

pytestmark = pytest.mark.integracion

GERENTE = encabezado(["gerente"], sub=GERENTE_ID)
INEXISTENTE = "b0000000-0000-4000-8000-0000000000ff"
FACTOR = Factor("relacion_ritmo", "El ritmo de avance es menor al necesario", 0.81)


def _prediccion(obra: str, riesgo: float | None, nivel: str, horas: int) -> Prediccion:
    return Prediccion(
        clave=f"{obra}:{horas}",
        obra_id=obra,
        riesgo_retraso=riesgo,
        nivel=nivel,
        factores=(FACTOR,) if riesgo is not None else (),
        variables={},
        version_modelo="rr-1.0.0",
        motivo="DIARIO",
        calculada_en=d.AHORA - timedelta(hours=horas),
    )


async def guardar_predicciones(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    almacen = AlmacenPrediccionesMongo(ia_db)
    await almacen.guardar(_prediccion(d.O1, 0.40, "MEDIO", 30))  # anterior: no cuenta
    await almacen.guardar(_prediccion(d.O1, 0.72, "ALTO", 9))
    await almacen.guardar(_prediccion(d.O2, 0.35, "MEDIO", 9))
    await almacen.guardar(_prediccion(d.O3, None, "SIN_DATOS", 9))  # O3 no está EN_EJECUCION


@pytest.fixture
async def app(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    await d.cargar(motor, ia_db)
    await guardar_predicciones(ia_db)
    async with app_ia(motor, url_lector, ia_db, url_mongo_pruebas) as (aplicacion, http):
        yield aplicacion, http


async def llamar(cliente: Client, herramienta: str, argumentos: dict[str, Any]) -> tuple[bool, Any]:
    resultado = await cliente.call_tool(herramienta, argumentos)
    texto = resultado.content[0].text  # type: ignore[union-attr]
    if not resultado.is_error:
        assert json.loads(texto) == resultado.structured_content
    return resultado.is_error, resultado.structured_content if not resultado.is_error else json.loads(texto)


# --- riesgo_retraso --------------------------------------------------------------------------------------------------


async def test_riesgo_retraso_igual_al_rest(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, http = app
    async with cliente_mcp(aplicacion) as cliente:
        error, salida = await llamar(cliente, "riesgo_retraso", {})
    assert not error
    assert salida["generadoEn"] == "2027-03-20T14:00:00Z"
    assert [(p["obraId"], p["obraNombre"], p["nivel"]) for p in salida["predicciones"]] == [
        (d.O1, d.NOMBRE_O1, "ALTO"),
        (d.O2, d.NOMBRE_O2, "MEDIO"),
    ]
    assert salida["total"] == 2
    assert salida["truncado"] is False
    rest = (await http.get(f"/predicciones?obraIds={d.O1},{d.O2}", headers=GERENTE)).json()
    for mcp, esperado in zip(salida["predicciones"], rest, strict=True):
        for campo in ("obraId", "probabilidad", "nivel", "factores", "versionModelo", "calculadaEn"):
            assert mcp[campo] == esperado[campo], campo


async def test_riesgo_retraso_filtros_limite_y_obra_inexistente(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    async with cliente_mcp(aplicacion) as cliente:
        _, solo_alto = await llamar(cliente, "riesgo_retraso", {"niveles": ["ALTO"]})
        _, recortado = await llamar(cliente, "riesgo_retraso", {"limite": 1})
        _, pedidas = await llamar(cliente, "riesgo_retraso", {"obraIds": [d.O2, d.O3]})
        error, inexistente = await llamar(cliente, "riesgo_retraso", {"obraIds": [d.O1, INEXISTENTE]})
    assert [p["obraId"] for p in solo_alto["predicciones"]] == [d.O1]
    assert (len(recortado["predicciones"]), recortado["total"], recortado["truncado"]) == (1, 2, True)
    assert [p["obraId"] for p in pedidas["predicciones"]] == [d.O2]  # O3 no está en ejecución
    assert error
    assert inexistente["codigo"] == "OBRA_NO_ENCONTRADA"


async def test_riesgo_retraso_sin_predicciones_es_lista_vacia(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> None:
    await d.cargar(motor, ia_db)
    async with app_ia(motor, url_lector, ia_db, url_mongo_pruebas) as (aplicacion, _), cliente_mcp(aplicacion) as c:
        error, salida = await llamar(c, "riesgo_retraso", {})
    assert not error
    assert (salida["predicciones"], salida["total"], salida["truncado"]) == ([], 0, False)


# --- consumos_atipicos ----------------------------------------------------------------------------------------------


async def test_consumos_atipicos_por_defecto_pendientes_30_dias_igual_al_rest(
    app: tuple[FastAPI, httpx.AsyncClient],
) -> None:
    aplicacion, http = app
    async with cliente_mcp(aplicacion) as cliente:
        error, salida = await llamar(cliente, "consumos_atipicos", {})
    assert not error
    assert [(c["obraId"], c["puntaje"]) for c in salida["consumos"]] == [(d.O2, 0.80), (d.O1, 0.71)]
    assert salida["consumos"][0]["obraNombre"] == d.NOMBRE_O2
    assert (salida["total"], salida["truncado"]) == (2, False)
    respuesta = await http.get("/consumos-atipicos?estado=PENDIENTE", headers=GERENTE)
    rest = {c["movimientoId"]: c for c in respuesta.json()}
    for consumo in salida["consumos"]:
        esperado = rest[consumo["movimientoId"]]
        for campo in ("obraId", "materialId", "partidaId", "cantidad", "periodo", "registradoEn", "puntaje", "motivos"):
            assert consumo[campo] == esperado[campo], campo
        assert consumo["estadoRevision"] == "PENDIENTE"


async def test_consumos_atipicos_filtros_y_truncado(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    async with cliente_mcp(aplicacion) as cliente:
        _, siete_dias = await llamar(cliente, "consumos_atipicos", {"dias": 7})
        _, confirmados = await llamar(cliente, "consumos_atipicos", {"estados": ["CONFIRMADO"]})
        _, de_o1 = await llamar(cliente, "consumos_atipicos", {"obraId": d.O1, "estados": ["PENDIENTE", "CONFIRMADO"]})
        _, uno = await llamar(cliente, "consumos_atipicos", {"limite": 1})
        error, inexistente = await llamar(cliente, "consumos_atipicos", {"obraId": INEXISTENTE})
    assert [c["obraId"] for c in siete_dias["consumos"]] == [d.O1]
    assert [c["estadoRevision"] for c in confirmados["consumos"]] == ["CONFIRMADO"]
    assert [c["puntaje"] for c in de_o1["consumos"]] == [0.71, 0.65]
    assert (len(uno["consumos"]), uno["total"], uno["truncado"]) == (1, 2, True)
    assert error
    assert inexistente["codigo"] == "OBRA_NO_ENCONTRADA"


ARGUMENTOS_INVALIDOS = [
    ("riesgo_retraso", {"obraIds": ["x' OR 1=1"]}),
    ("riesgo_retraso", {"limite": 1000}),
    ("riesgo_retraso", {"limite": "5"}),
    ("riesgo_retraso", {"niveles": ["CRITICO"]}),
    ("riesgo_retraso", {"obraIds": []}),
    ("riesgo_retraso", {"campoDeMas": 1}),
    ("consumos_atipicos", {"obraId": "x' OR 1=1"}),
    ("consumos_atipicos", {"obraId": {"$ne": None}}),
    ("consumos_atipicos", {"estados": [{"$ne": None}]}),
    ("consumos_atipicos", {"dias": 91}),
    ("consumos_atipicos", {"limite": 0}),
    ("consumos_atipicos", {"obra_id": d.O1}),
]


@pytest.mark.parametrize(("herramienta", "argumentos"), ARGUMENTOS_INVALIDOS)
async def test_argumentos_invalidos(
    app: tuple[FastAPI, httpx.AsyncClient], herramienta: str, argumentos: dict[str, Any]
) -> None:
    aplicacion, _ = app
    async with cliente_mcp(aplicacion) as cliente:
        error, salida = await llamar(cliente, herramienta, argumentos)
    assert error
    assert salida["codigo"] == "ARGUMENTO_INVALIDO"
    assert "1=1" not in salida["mensaje"]
