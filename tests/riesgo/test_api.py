"""Endpoints del riesgo (402) con la API real (ASGI), PostgreSQL y MongoDB reales y tokens firmados en la prueba."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.eventos import OBRA_1, OBRA_2, Eventos, obra_registrada
from apoyo.tokens import GERENTE_ID, RESIDENTE_ID, encabezado, validador
from ia.config import Configuracion
from ia.main import crear_app
from ia.riesgo.aplicacion.puertos import Prediccion
from ia.riesgo.dominio.variables import Factor
from ia.riesgo.infraestructura.modelo_xgboost import cargar_modelo
from ia.riesgo.infraestructura.predicciones_mongo import AlmacenPrediccionesMongo

pytestmark = pytest.mark.integracion

MODELOS = Path(__file__).resolve().parents[2] / "modelos"
OBRA_3 = "b0000000-0000-4000-8000-000000000003"
OTRO_RESIDENTE = "a0000000-0000-4000-8000-000000000004"
AHORA = datetime(2026, 11, 15, 5, 0, tzinfo=UTC)
FACTOR = Factor(
    "relacion_ritmo", "El ritmo de avance de los últimos meses es menor al necesario para terminar a tiempo", 0.81
)


def _prediccion(obra: str, riesgo: float | None, nivel: str, dias: int, clave: str) -> Prediccion:
    return Prediccion(
        clave=clave,
        obra_id=obra,
        riesgo_retraso=riesgo,
        nivel=nivel,
        factores=(FACTOR,) if riesgo is not None else (),
        variables={},
        version_modelo="rr-1.0.0",
        motivo="DIARIO",
        calculada_en=AHORA + timedelta(days=dias),
    )


@pytest.fixture
async def datos(motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada(OBRA_1))  # residente1
    otro = obra_registrada(OBRA_2)
    otro["asignaciones"] = {**otro["asignaciones"], "residenteId": OTRO_RESIDENTE}
    await eventos.publicar("ObraRegistrada", otro)
    await eventos.publicar("ObraRegistrada", obra_registrada(OBRA_3))  # sin predicción
    almacen = AlmacenPrediccionesMongo(ia_db)
    await almacen.guardar(_prediccion(OBRA_1, 0.65, "MEDIO", 0, "o1:1"))
    await almacen.guardar(_prediccion(OBRA_1, None, "SIN_DATOS", 1, "o1:2"))
    await almacen.guardar(_prediccion(OBRA_1, 0.72, "ALTO", 2, "o1:3"))
    await almacen.guardar(_prediccion(OBRA_2, 0.10, "BAJO", 0, "o2:1"))


@pytest.fixture
async def cliente(
    motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> AsyncIterator[httpx.AsyncClient]:
    config = Configuracion(mongo_url=url_mongo_pruebas, mongo_db=ia_db.name)
    app: FastAPI = crear_app(config, validador=validador(), motor=motor, modelo_riesgo=cargar_modelo(MODELOS))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ia") as http,
    ):
        yield http


async def test_predicciones_en_lote_solo_visibles_y_con_prediccion(datos: None, cliente: httpx.AsyncClient) -> None:
    ids = f"{OBRA_2},{OBRA_1},{OBRA_3},b0000000-0000-4000-8000-000000000009"
    respuesta = await cliente.get(
        "/predicciones", params={"obraIds": ids}, headers=encabezado(["gerente"], sub=GERENTE_ID)
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert [p["obraId"] for p in cuerpo] == [OBRA_2, OBRA_1]
    assert cuerpo[1] == {
        "obraId": OBRA_1,
        "probabilidad": 0.72,
        "nivel": "ALTO",
        "factores": [{"variable": FACTOR.variable, "descripcion": FACTOR.descripcion, "peso": 0.81}],
        "versionModelo": "rr-1.0.0",
        "calculadaEn": "2026-11-17T05:00:00Z",
    }


async def test_residente_no_ve_obras_ajenas(datos: None, cliente: httpx.AsyncClient) -> None:
    residente = encabezado(["residente"], sub=RESIDENTE_ID)
    lote = await cliente.get("/predicciones", params={"obraIds": f"{OBRA_1},{OBRA_2}"}, headers=residente)
    assert [p["obraId"] for p in lote.json()] == [OBRA_1]
    ajena = await cliente.get(f"/obras/{OBRA_2}/prediccion", headers=residente)
    assert ajena.status_code == 404
    assert ajena.json()["codigo"] == "OBRA_NO_ENCONTRADA"


async def test_prediccion_de_una_obra_con_historial_sin_los_sin_datos(datos: None, cliente: httpx.AsyncClient) -> None:
    respuesta = await cliente.get(f"/obras/{OBRA_1}/prediccion", headers=encabezado(["admin"]))
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert (cuerpo["nivel"], cuerpo["probabilidad"]) == ("ALTO", 0.72)
    assert cuerpo["historial"] == [
        {"probabilidad": 0.65, "calculadaEn": "2026-11-15T05:00:00Z"},
        {"probabilidad": 0.72, "calculadaEn": "2026-11-17T05:00:00Z"},
    ]
    corto = await cliente.get(f"/obras/{OBRA_1}/prediccion", params={"historial": 1}, headers=encabezado(["admin"]))
    assert [p["probabilidad"] for p in corto.json()["historial"]] == [0.72]
    fuera = await cliente.get(f"/obras/{OBRA_1}/prediccion", params={"historial": 91}, headers=encabezado(["admin"]))
    assert fuera.status_code == 400


async def test_obra_visible_sin_prediccion_es_sin_datos(datos: None, cliente: httpx.AsyncClient) -> None:
    respuesta = await cliente.get(f"/obras/{OBRA_3}/prediccion", headers=encabezado(["gerente"], sub=GERENTE_ID))
    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "obraId": OBRA_3,
        "probabilidad": None,
        "nivel": "SIN_DATOS",
        "factores": [],
        "versionModelo": None,
        "calculadaEn": None,
        "historial": [],
    }


async def test_obra_inexistente_o_id_invalido_es_404(datos: None, cliente: httpx.AsyncClient) -> None:
    for obra in ("b0000000-0000-4000-8000-000000000009", "no-es-uuid"):
        respuesta = await cliente.get(f"/obras/{obra}/prediccion", headers=encabezado(["admin"]))
        assert respuesta.status_code == 404
        assert respuesta.json()["codigo"] == "OBRA_NO_ENCONTRADA"


async def test_demasiadas_obras_y_lista_invalida(cliente: httpx.AsyncClient) -> None:
    admin = encabezado(["admin"])
    muchas = ",".join(f"b0000000-0000-4000-8000-{i:012d}" for i in range(101))
    demasiadas = await cliente.get("/predicciones", params={"obraIds": muchas}, headers=admin)
    assert demasiadas.status_code == 400
    assert demasiadas.json()["codigo"] == "DEMASIADAS_OBRAS"
    cien = ",".join(f"b0000000-0000-4000-8000-{i:012d}" for i in range(100))
    assert (await cliente.get("/predicciones", params={"obraIds": cien}, headers=admin)).status_code == 200
    for valor in (None, "", "no-es-uuid"):
        params = {} if valor is None else {"obraIds": valor}
        respuesta = await cliente.get("/predicciones", params=params, headers=admin)
        assert respuesta.status_code == 400
        assert respuesta.json()["codigo"] == "DATOS_INVALIDOS"


async def test_modelo_con_metricas_y_datos_sinteticos(cliente: httpx.AsyncClient) -> None:
    respuesta = await cliente.get("/modelos/riesgo-retraso", headers=encabezado(["residente"], sub=RESIDENTE_ID))
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["versionModelo"] == "rr-1.0.0"
    assert cuerpo["datos"] == "SINTETICOS"
    assert cuerpo["filasEntrenamiento"] == 9600
    assert set(cuerpo["metricas"]) == {"aucRoc", "aucRocRegla", "recallMedio", "precisionAlto", "brier"}
    assert cuerpo["umbrales"] == {"medio": 0.3, "alto": 0.6}
    assert {"nombre": "relacion_ritmo", "descripcion": "Ritmo reciente / ritmo necesario"} in cuerpo["variables"]
    assert cuerpo["entrenadoEn"].endswith("Z")


async def test_sin_modelo_responde_503(motor: AsyncEngine, url_mongo_pruebas: str) -> None:
    config = Configuracion(mongo_url=url_mongo_pruebas, mongo_db="ia_prueba_sin_modelo")
    app = crear_app(config, validador=validador(), motor=motor, cargar_modelo_riesgo=False)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ia") as http,
    ):
        respuesta = await http.get("/modelos/riesgo-retraso", headers=encabezado(["admin"]))
    assert respuesta.status_code == 503
    assert respuesta.json()["codigo"] == "MODELO_NO_DISPONIBLE"


@pytest.mark.parametrize(
    "ruta", ["/predicciones?obraIds=" + OBRA_1, f"/obras/{OBRA_1}/prediccion", "/modelos/riesgo-retraso"]
)
async def test_permisos(cliente: httpx.AsyncClient, ruta: str) -> None:
    assert (await cliente.get(ruta)).status_code == 401
    for rol in ("cliente", "maestro", "almacenero"):
        assert (await cliente.get(ruta, headers=encabezado([rol], azp=None))).status_code == 403
