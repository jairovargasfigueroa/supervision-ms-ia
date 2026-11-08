"""Pruebas del diseño de la 503 que faltan en las demás: cada atajo de punta a punta, recetas inválidas sin ninguna
consulta y permisos del usuario `ia_reportes_lector`."""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo import reportes_503 as d
from apoyo.tokens import GERENTE_ID, encabezado
from constructor_reportes.apoyo_api import DIRECTORIO, cliente_api
from dobles.modelo_simulado import ModeloSimulado
from ia.constructor_reportes.infraestructura.ejecutor_sql import EjecutorReportes

pytestmark = pytest.mark.integracion

GERENTE = encabezado(["gerente"], sub=GERENTE_ID)


@pytest.fixture
async def api(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI, ModeloSimulado]]:
    await d.cargar(motor, ia_db)
    modelo = ModeloSimulado()
    async with cliente_api(motor, url_lector, ia_db, url_mongo_pruebas, modelo) as (http, app):
        yield http, app, modelo


ESPERADO = {
    "AVANCE_OBRAS": 2,
    "OBRAS_EN_RIESGO": 2,
    "CONSUMO_MATERIAL_MES": 3,
    "SALIDAS_SIN_STOCK": 1,
    "COBROS_VENCIDOS": 1,
    "COBROS_POR_VENCER": 1,
    "COBRADO_POR_OBRA": 2,
    "PLANILLAS_OBSERVADAS": 2,
    "PLANILLAS_APROBADAS": 1,
    "CONSUMOS_ATIPICOS_PENDIENTES": 2,
}


async def test_cada_atajo_de_punta_a_punta(api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado]) -> None:
    http, app, modelo = api
    for atajo, filas in ESPERADO.items():
        app.state.modelo_lenguaje = ModeloSimulado()  # sin guion: el resumen cae a la plantilla
        respuesta = await http.post(
            "/consultas-reporte", json={"atajo": atajo, "directorio": DIRECTORIO}, headers=GERENTE
        )
        assert respuesta.status_code == 200, (atajo, respuesta.text)
        cuerpo = respuesta.json()
        assert cuerpo["totalFilas"] == filas, (atajo, cuerpo["filas"])
        assert cuerpo["resumenPlantilla"] is True
        assert all(len(f) == len(cuerpo["columnas"]) for f in cuerpo["filas"])
        llamadas = app.state.modelo_lenguaje.llamadas_de("generar_json")
        assert len(llamadas) == 1 and llamadas[0].esquema is not None and "resumen" in llamadas[0].esquema["properties"]
    assert modelo.llamadas == []


RECETAS_INVALIDAS: list[tuple[dict[str, Any], str]] = [
    ({"conjunto": "USUARIOS"}, "INTENCION_NO_RECONOCIDA"),
    (
        {"conjunto": "CONSUMO_MATERIAL", "filtros": [{"campo": "estado", "operador": "IGUAL", "valor": "X"}]},
        "RECETA_INVALIDA",
    ),
    ({"conjunto": "OBRAS", "columnas": ["numero"]}, "RECETA_INVALIDA"),
    ({"conjunto": "OBRAS", "filtros": [{"campo": "zona", "operador": "CONTIENE", "valor": "NOR"}]}, "RECETA_INVALIDA"),
    ({"conjunto": "OBRAS", "columnas": ["obra"], "orden": [{"por": "zona"}]}, "RECETA_INVALIDA"),
    ({"conjunto": "OBRAS", "limite": 1000}, "INTENCION_NO_RECONOCIDA"),
    (
        {"conjunto": "PLANILLAS", "filtros": [{"campo": "periodo", "operador": "IGUAL", "valor": "2027-09"}]},
        "RECETA_INVALIDA",
    ),
    (
        {
            "conjunto": "PLANILLAS",
            "filtros": [
                {"campo": "periodo", "operador": "DESDE", "valor": "2027-03"},
                {"campo": "periodo", "operador": "HASTA", "valor": "2027-01"},
            ],
        },
        "RECETA_INVALIDA",
    ),
    ({"conjunto": "OBRAS", "filtros": [{"campo": "zona", "operador": "IGUAL", "valor": "NORESTE"}]}, "RECETA_INVALIDA"),
    ({"conjunto": "OBRAS", "metricas": [{"funcion": "CONTEO"}]}, "RECETA_INVALIDA"),
    ({"conjunto": "COBROS", "campoDeMas": 1}, "INTENCION_NO_RECONOCIDA"),
]


async def test_recetas_invalidas_no_consultan(
    api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, app, _ = api
    consultas: list[Any] = []

    async def espia(self: EjecutorReportes, consulta: Any) -> Any:
        consultas.append(consulta)
        raise AssertionError("no debía consultar")

    monkeypatch.setattr(EjecutorReportes, "ejecutar", espia)
    for i, (receta, codigo) in enumerate(RECETAS_INVALIDAS):
        app.state.modelo_lenguaje = ModeloSimulado(json_=[receta])
        usuario = encabezado(["gerente"], sub=f"a0000000-0000-4000-8000-0000000001{i:02d}")  # 10 por minuto por usuario
        respuesta = await http.post("/consultas-reporte", json={"texto": "un pedido cualquiera"}, headers=usuario)
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (422, codigo), receta
    assert consultas == []


def test_el_lector_no_escribe_ni_lee_tablas_internas(url_lector: str) -> None:
    motor = create_engine(url_lector)
    prohibidas = [
        "INSERT INTO rpt_cobro (cobro_id, actualizado_en) VALUES (gen_random_uuid(), now())",
        "UPDATE rpt_obra_resumen SET nombre = 'x'",
        "DELETE FROM rpt_movimiento",
        "DROP TABLE rpt_cobro",
        "CREATE TABLE intrusa (id int)",
        "SELECT * FROM evento_aplicado",
        "SELECT * FROM carga_inicial",
    ]
    try:
        with motor.connect() as conexion:
            for sentencia in prohibidas:
                with pytest.raises(DBAPIError):
                    conexion.execute(text(sentencia))
                conexion.rollback()
            assert conexion.execute(text("SHOW default_transaction_read_only")).scalar() == "on"
    finally:
        motor.dispose()
