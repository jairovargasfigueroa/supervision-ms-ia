"""Endpoints de los agentes (502) en `ia-api`: roles, visibilidad del residente, «Ejecutar ahora», hallazgos vigentes,
sugerencias y la decisión humana (aprobar solo registra)."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo import reportes_503 as d
from apoyo.tokens import ADMIN_ID, GERENTE_ID, encabezado
from ia.agentes.dominio.ejecucion import MANUAL, PROGRAMADA, Ejecucion, Evidencia, HallazgoGuardado, Sugerencia
from ia.agentes.infraestructura.repositorio_mongo import RepositorioEjecucionesMongo, RepositorioSugerenciasMongo
from ia.reportes.infraestructura.tablas import rpt_obra_resumen
from protocolo_mcp.apoyo_mcp import app_ia, encabezado_agente

pytestmark = pytest.mark.integracion

GERENTE = encabezado(["gerente"], sub=GERENTE_ID)
ADMIN = encabezado(["admin"], sub=ADMIN_ID)
RESIDENTE = encabezado(["residente"], sub=d.R1)
OTRO = "a0000000-0000-4000-8000-000000000009"


def _hallazgo(obra: str, tipo: str) -> HallazgoGuardado:
    return HallazgoGuardado(obra, "Obra", tipo, "ALTA", "Situación", "Causa", (Evidencia("avance_obra", "dato"),))


def _sugerencia(agente: str, obra: str, accion: str, minutos: int) -> Sugerencia:
    return Sugerencia(
        f"s-{agente}-{accion}",
        "e",
        agente,
        obra,
        "Obra",
        accion,
        "Descripción",
        0,
        d.AHORA + timedelta(minutes=minutos),
    )


@pytest.fixture
async def api(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI, dict[str, str]]]:
    await d.cargar(motor, ia_db)
    async with motor.begin() as conexion:  # O2 es de otro residente
        await conexion.execute(
            update(rpt_obra_resumen).where(rpt_obra_resumen.c.obra_id == d.O2).values(residente_id=OTRO)
        )
    ejecuciones = RepositorioEjecucionesMongo(ia_db)
    sugerencias = RepositorioSugerenciasMongo(ia_db)
    ids: dict[str, str] = {}
    async with app_ia(motor, url_lector, ia_db, url_mongo_pruebas) as (app, http):
        for agente, hallazgos in (
            ("SEGUIMIENTO_OBRAS", (_hallazgo(d.O1, "ATRASO"), _hallazgo(d.O2, "RIESGO_DE_ATRASO"))),
            ("CONTROL_COBROS", (_hallazgo(d.O1, "COBRO_VENCIDO"),)),
        ):
            vieja = Ejecucion.nueva(agente, PROGRAMADA, None, d.AHORA - timedelta(days=1), "ollama", "qwen3:8b")
            await ejecuciones.crear(vieja)
            await ejecuciones.terminar(
                vieja.id,
                "Ayer",
                [_hallazgo(d.O1, "ATRASO" if agente == "SEGUIMIENTO_OBRAS" else "COBRO_POR_VENCER")],
                d.AHORA - timedelta(days=1),
            )
            nueva = Ejecucion.nueva(agente, PROGRAMADA, None, d.AHORA, "ollama", "qwen3:8b")
            await ejecuciones.crear(nueva)
            await ejecuciones.terminar(nueva.id, "Hoy", list(hallazgos), d.AHORA + timedelta(minutes=2))
            ids[agente] = nueva.id
        for s in (
            _sugerencia("SEGUIMIENTO_OBRAS", d.O1, "PROGRAMAR_VISITA", 1),
            _sugerencia("SEGUIMIENTO_OBRAS", d.O2, "REVISAR_PROGRAMA_OBRA", 2),
            _sugerencia("CONTROL_COBROS", d.O1, "CONTACTAR_CLIENTE_COBRO", 3),
        ):
            await sugerencias.crear(s)
        yield http, app, ids


async def test_agentes_para_gerente_y_residente(api: tuple[httpx.AsyncClient, FastAPI, dict[str, str]]) -> None:
    http, _, ids = api
    gerente = (await http.get("/agentes", headers=GERENTE)).json()
    assert [a["agente"] for a in gerente] == ["SEGUIMIENTO_OBRAS", "CONTROL_MATERIALES", "CONTROL_COBROS"]
    seguimiento = gerente[0]
    assert seguimiento["nombre"] == "Seguimiento de obras"
    assert seguimiento["herramientas"] == [
        "avance_obra",
        "buscar_diario",
        "planillas_estado",
        "riesgo_retraso",
        "consultar_reporte",
    ]
    assert seguimiento["ultimaEjecucion"]["id"] == ids["SEGUIMIENTO_OBRAS"]
    assert seguimiento["ultimaEjecucion"]["cantidadHallazgos"] == 2
    assert seguimiento["sugerenciasPendientes"] == 2
    assert gerente[1]["ultimaEjecucion"] is None
    residente = (await http.get("/agentes", headers=RESIDENTE)).json()
    assert [a["agente"] for a in residente] == ["SEGUIMIENTO_OBRAS", "CONTROL_MATERIALES"]
    assert residente[0]["sugerenciasPendientes"] == 1  # solo sus obras
    assert residente[0]["ultimaEjecucion"]["cantidadHallazgos"] == 1  # el hallazgo de O2 no es de su obra


async def test_ejecutar_ahora(api: tuple[httpx.AsyncClient, FastAPI, dict[str, str]]) -> None:
    http, _, _ = api
    respuesta = await http.post("/agentes/CONTROL_MATERIALES/ejecuciones", headers=GERENTE)
    assert respuesta.status_code == 202
    corta = respuesta.json()
    assert (corta["agente"], corta["origen"], corta["estado"], corta["cantidadHallazgos"]) == (
        "CONTROL_MATERIALES",
        "MANUAL",
        "EN_CURSO",
        0,
    )
    assert corta["terminadaEn"] is None
    detalle = (await http.get(f"/ejecuciones/{corta['id']}", headers=GERENTE)).json()
    assert (detalle["solicitadaPor"], detalle["proveedorLlm"], detalle["modelo"]) == (GERENTE_ID, "ollama", "simulado")
    otra = await http.post("/agentes/CONTROL_MATERIALES/ejecuciones", headers=ADMIN)
    assert (otra.status_code, otra.json()["codigo"]) == (409, "EJECUCION_EN_CURSO")
    invalido = await http.post("/agentes/BORRAR_TODO/ejecuciones", headers=GERENTE)
    assert (invalido.status_code, invalido.json()["codigo"]) == (400, "AGENTE_INVALIDO")
    assert (await http.post("/agentes/CONTROL_COBROS/ejecuciones", headers=RESIDENTE)).status_code == 403


async def test_ejecuciones_solo_direccion(api: tuple[httpx.AsyncClient, FastAPI, dict[str, str]]) -> None:
    http, _, ids = api
    lista = (await http.get("/ejecuciones", params={"agente": "SEGUIMIENTO_OBRAS"}, headers=GERENTE)).json()
    assert [e["iniciadaEn"] for e in lista] == ["2027-03-20T14:00:00Z", "2027-03-19T14:00:00Z"]
    assert len((await http.get("/ejecuciones", params={"limite": 1}, headers=GERENTE)).json()) == 1
    for parametros in ({"limite": 51}, {"estado": "BORRADA"}, {"agente": "OTRO"}):
        assert (await http.get("/ejecuciones", params=parametros, headers=GERENTE)).status_code == 400
    detalle = (await http.get(f"/ejecuciones/{ids['CONTROL_COBROS']}", headers=ADMIN)).json()
    assert detalle["hallazgos"][0]["tipo"] == "COBRO_VENCIDO"
    assert detalle["error"] is None
    no_existe = await http.get("/ejecuciones/no-existe", headers=GERENTE)
    assert (no_existe.status_code, no_existe.json()["codigo"]) == (404, "EJECUCION_NO_ENCONTRADA")
    assert (await http.get(f"/ejecuciones/{ids['CONTROL_COBROS']}", headers=RESIDENTE)).status_code == 403
    assert (await http.get("/ejecuciones", headers=RESIDENTE)).status_code == 403


async def test_hallazgos_vigentes_con_visibilidad(api: tuple[httpx.AsyncClient, FastAPI, dict[str, str]]) -> None:
    http, _, ids = api
    gerente = (await http.get("/hallazgos", headers=GERENTE)).json()
    assert [(h["agente"], h["obraId"], h["tipo"]) for h in gerente] == [
        ("SEGUIMIENTO_OBRAS", d.O1, "ATRASO"),
        ("SEGUIMIENTO_OBRAS", d.O2, "RIESGO_DE_ATRASO"),
        ("CONTROL_COBROS", d.O1, "COBRO_VENCIDO"),
    ]
    assert gerente[0]["ejecucionId"] == ids["SEGUIMIENTO_OBRAS"]
    assert gerente[0]["detectadoEn"] == "2027-03-20T14:02:00Z"
    por_obra = (
        await http.get("/hallazgos", params={"obraId": d.O1, "agente": "CONTROL_COBROS"}, headers=GERENTE)
    ).json()
    assert [h["tipo"] for h in por_obra] == ["COBRO_VENCIDO"]
    residente = (await http.get("/hallazgos", headers=RESIDENTE)).json()
    assert [(h["agente"], h["obraId"]) for h in residente] == [("SEGUIMIENTO_OBRAS", d.O1)]
    assert (await http.get("/hallazgos", params={"agente": "CONTROL_COBROS"}, headers=RESIDENTE)).json() == []


async def test_sugerencias_con_visibilidad(api: tuple[httpx.AsyncClient, FastAPI, dict[str, str]]) -> None:
    http, _, _ = api
    gerente = (await http.get("/sugerencias", headers=GERENTE)).json()
    assert [s["accion"] for s in gerente] == ["CONTACTAR_CLIENTE_COBRO", "REVISAR_PROGRAMA_OBRA", "PROGRAMAR_VISITA"]
    assert gerente[2]["accionTexto"] == "Programar una visita de supervisión a la obra"
    assert gerente[2]["decididaPor"] is None
    filtro = {"agente": "SEGUIMIENTO_OBRAS", "obraId": d.O1, "estado": "PENDIENTE"}
    assert [s["accion"] for s in (await http.get("/sugerencias", params=filtro, headers=GERENTE)).json()] == [
        "PROGRAMAR_VISITA"
    ]
    residente = (await http.get("/sugerencias", headers=RESIDENTE)).json()
    assert [s["accion"] for s in residente] == ["PROGRAMAR_VISITA"]
    assert (await http.get("/sugerencias", params={"obraId": d.O2}, headers=RESIDENTE)).json() == []
    for parametros in ({"limite": 101}, {"estado": "OTRO"}):
        assert (await http.get("/sugerencias", params=parametros, headers=GERENTE)).status_code == 400


async def test_aprobar_y_rechazar(api: tuple[httpx.AsyncClient, FastAPI, dict[str, str]]) -> None:
    http, _, _ = api
    visita = "s-SEGUIMIENTO_OBRAS-PROGRAMAR_VISITA"
    largo = await http.post(f"/sugerencias/{visita}/aprobar", json={"comentario": "x" * 301}, headers=RESIDENTE)
    assert (largo.status_code, largo.json()["codigo"]) == (422, "COMENTARIO_INVALIDO")
    assert largo.json()["errores"][0]["campo"] == "comentario"
    aprobada = await http.post(
        f"/sugerencias/{visita}/aprobar", json={"comentario": "Vamos el lunes"}, headers=RESIDENTE
    )
    assert aprobada.status_code == 200
    cuerpo = aprobada.json()
    assert (cuerpo["estado"], cuerpo["decididaPor"], cuerpo["comentario"]) == ("APROBADA", d.R1, "Vamos el lunes")
    assert cuerpo["decididaEn"] == "2027-03-20T14:00:00Z"
    otra_vez = await http.post(f"/sugerencias/{visita}/rechazar", json={"motivo": "Ya no hace falta"}, headers=GERENTE)
    assert (otra_vez.status_code, otra_vez.json()["codigo"]) == (409, "SUGERENCIA_YA_DECIDIDA")

    ajena = "s-SEGUIMIENTO_OBRAS-REVISAR_PROGRAMA_OBRA"
    cobros = "s-CONTROL_COBROS-CONTACTAR_CLIENTE_COBRO"
    for sugerencia in (ajena, cobros, "no-existe"):
        respuesta = await http.post(f"/sugerencias/{sugerencia}/aprobar", headers=RESIDENTE)
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (404, "SUGERENCIA_NO_ENCONTRADA")

    corto = await http.post(f"/sugerencias/{cobros}/rechazar", json={"motivo": "corto"}, headers=GERENTE)
    assert (corto.status_code, corto.json()["codigo"]) == (422, "MOTIVO_INVALIDO")
    assert (await http.post(f"/sugerencias/{cobros}/rechazar", headers=GERENTE)).json()["codigo"] == "MOTIVO_INVALIDO"
    rechazada = await http.post(f"/sugerencias/{cobros}/rechazar", json={"motivo": "El cliente ya pagó"}, headers=ADMIN)
    assert (rechazada.json()["estado"], rechazada.json()["comentario"]) == ("RECHAZADA", "El cliente ya pagó")
    sin_cuerpo = await http.post(f"/sugerencias/{ajena}/aprobar", headers=GERENTE)
    assert (sin_cuerpo.status_code, sin_cuerpo.json()["comentario"]) == (200, None)


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("GET", "/agentes"),
        ("GET", "/hallazgos"),
        ("GET", "/sugerencias"),
        ("POST", "/agentes/CONTROL_COBROS/ejecuciones"),
    ],
)
async def test_agente_ia_y_sin_token(
    api: tuple[httpx.AsyncClient, FastAPI, dict[str, str]], metodo: str, ruta: str
) -> None:
    http, _, _ = api
    assert (await http.request(metodo, ruta, headers=encabezado_agente())).status_code == 403
    assert (await http.request(metodo, ruta)).status_code == 401


async def test_ejecucion_corta_de_origen_manual(api: tuple[httpx.AsyncClient, FastAPI, dict[str, str]]) -> None:
    http, _, _ = api
    await http.post("/agentes/SEGUIMIENTO_OBRAS/ejecuciones", headers=GERENTE)
    manuales = (await http.get("/ejecuciones", params={"estado": "EN_CURSO"}, headers=GERENTE)).json()
    assert [e["origen"] for e in manuales] == [MANUAL]
