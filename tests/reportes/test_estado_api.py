"""`GET /reportes/estado` con la API real (ASGI), PostgreSQL real y tokens firmados en la prueba."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.eventos import Eventos, movimiento, obra_registrada
from apoyo.tokens import encabezado, validador
from ia.config import Configuracion
from ia.main import crear_app
from ia.reportes.infraestructura.repositorio_sql import BaseReportesSql

pytestmark = pytest.mark.integracion


@pytest.fixture
async def cliente(motor: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = crear_app(Configuracion(), validador=validador(), motor=motor)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ia") as cliente,
    ):
        yield cliente


async def test_admin_ve_el_estado_sin_carga_inicial(cliente: httpx.AsyncClient) -> None:
    respuesta = await cliente.get("/reportes/estado", headers=encabezado(["admin"]))
    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "tablas": {"rptObraResumen": 0, "rptPlanilla": 0, "rptConsumoMaterial": 0, "rptMovimiento": 0, "rptCobro": 0},
        "ultimosEventos": [],
        "cargaInicial": None,
    }


async def test_admin_ve_filas_ultimos_eventos_y_la_ultima_carga(cliente: httpx.AsyncClient, motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    await eventos.publicar("MovimientoRegistrado", movimiento())
    async with BaseReportesSql(motor).transaccion() as repo:
        await repo.iniciar_carga("0f000000-0000-4000-8000-000000000001", eventos.reloj.ahora())
        await repo.terminar_carga(
            "0f000000-0000-4000-8000-000000000001",
            "TERMINADA",
            3,
            {"rptObraResumen": 3, "rptPlanilla": 4, "rptMovimiento": 67, "rptCobro": 3},
            None,
            eventos.reloj.ahora(),
        )

    respuesta = await cliente.get("/reportes/estado", headers=encabezado(["admin"]))
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["tablas"] == {
        "rptObraResumen": 1,
        "rptPlanilla": 0,
        "rptConsumoMaterial": 1,
        "rptMovimiento": 1,
        "rptCobro": 0,
    }
    assert [e["tipo"] for e in cuerpo["ultimosEventos"]] == ["MovimientoRegistrado", "ObraRegistrada"]
    assert cuerpo["ultimosEventos"][0]["aplicadoEn"] == "2026-10-28T16:00:00Z"
    assert cuerpo["cargaInicial"] == {
        "cargaId": "0f000000-0000-4000-8000-000000000001",
        "estado": "TERMINADA",
        "iniciadaEn": "2026-10-28T16:00:00Z",
        "terminadaEn": "2026-10-28T16:00:00Z",
        "obras": 3,
        "filas": {"rptObraResumen": 3, "rptPlanilla": 4, "rptMovimiento": 67, "rptCobro": 3},
        "error": None,
    }


@pytest.mark.parametrize("rol", ["gerente", "residente"])
async def test_otros_roles_reciben_403(cliente: httpx.AsyncClient, rol: str) -> None:
    respuesta = await cliente.get("/reportes/estado", headers=encabezado([rol]))
    assert respuesta.status_code == 403
    assert respuesta.headers["content-type"] == "application/problem+json"
    assert respuesta.json()["codigo"] == "SIN_PERMISO"


async def test_sin_token_responde_401(cliente: httpx.AsyncClient) -> None:
    respuesta = await cliente.get("/reportes/estado")
    assert respuesta.status_code == 401
    assert respuesta.headers["content-type"] == "application/problem+json"
    cuerpo = respuesta.json()
    assert cuerpo["codigo"] == "NO_AUTENTICADO"
    assert cuerpo["status"] == 401
    assert cuerpo["type"] == "about:blank"


async def test_salud_sigue_sin_token(cliente: httpx.AsyncClient) -> None:
    respuesta = await cliente.get("/salud")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok"}
