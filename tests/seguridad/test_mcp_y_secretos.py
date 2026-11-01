"""Servidor MCP de IA (504, M1 a M6) y secretos (K1, K2)."""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import httpx2
import pytest
from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from agentes.apoyo_agentes import O1, entorno, reporte_seguimiento, servidores_de_prueba
from apoyo import reportes_503 as d
from apoyo.tokens import ADMIN_ID, GERENTE_ID, encabezado
from ia import main, worker
from ia.agentes.aplicacion.ejecutor import EjecutorAgente
from ia.agentes.infraestructura.conector_mcp import ConectorMcpReal
from ia.atipicos.aplicacion.consultas import ConsultarConsumosAtipicos
from ia.comun.registros import silenciar_librerias
from ia.constructor_reportes.aplicacion.motor import MotorReportes
from ia.lenguaje.infraestructura.anthropic import ModeloAnthropic
from ia.mcp.cliente.token import TokenAgentes
from ia.riesgo.aplicacion.consultas import PrediccionesEnEjecucion
from protocolo_mcp.apoyo_mcp import ENCABEZADOS_MCP, PETICION_LISTA, app_ia, cliente_mcp, encabezado_agente, http_mcp
from protocolo_mcp.test_herramientas_ia import guardar_predicciones, llamar
from protocolo_mcp.test_servidor_contrato import _huella

CLAVE_ANTHROPIC = "sk-ant-api03-clave-de-prueba-no-real"
SECRETO_AGENTES = "secreto-del-cliente-ia-agentes"
TOKEN_AGENTES = "token-de-acceso-de-ia-agentes"


@pytest.fixture
async def app(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    await d.cargar(motor, ia_db)
    await guardar_predicciones(ia_db)
    async with app_ia(motor, url_lector, ia_db, url_mongo_pruebas) as (aplicacion, http):
        yield aplicacion, http


@pytest.mark.integracion
async def test_m1_m2_sin_token_401_y_personas_403(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    casos = [({}, 401), (encabezado(["gerente"], sub=GERENTE_ID), 403), (encabezado(["admin"], sub=ADMIN_ID), 403)]
    for encabezados, estado in casos:
        async with http_mcp(aplicacion, {**ENCABEZADOS_MCP, **encabezados}) as http:
            assert (await http.post("/mcp", json=PETICION_LISTA)).status_code == estado


@pytest.mark.integracion
async def test_m3_herramienta_que_no_existe_es_error_del_protocolo(app: tuple[FastAPI, httpx.AsyncClient]) -> None:
    aplicacion, _ = app
    async with http_mcp(aplicacion, {**ENCABEZADOS_MCP, **encabezado_agente()}) as http:
        peticion = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "borrar_obra", "arguments": {}},
        }
        respuesta = (await http.post("/mcp", json=peticion)).json()
    assert "result" not in respuesta
    assert respuesta["error"]["code"] == -32602
    assert "desconocida" in respuesta["error"]["message"]


def _filtro(campo: str, valor: Any) -> list[dict[str, Any]]:
    return [{"campo": campo, "operador": "IGUAL", "valor": valor}]


@pytest.mark.integracion
async def test_m4_argumentos_con_inyeccion_sin_consulta(
    app: tuple[FastAPI, httpx.AsyncClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    aplicacion, _ = app
    consultas: list[str] = []

    def espia(nombre: str) -> Any:
        async def falla(*_: Any, **__: Any) -> Any:
            consultas.append(nombre)
            raise AssertionError("no debía consultar")

        return falla

    monkeypatch.setattr(PrediccionesEnEjecucion, "consultar", espia("riesgo"))
    monkeypatch.setattr(ConsultarConsumosAtipicos, "para_agentes", espia("atipicos"))
    monkeypatch.setattr(MotorReportes, "ejecutar", espia("reporte"))
    casos = [
        ("riesgo_retraso", {"obraIds": ["x' OR 1=1"]}),
        ("riesgo_retraso", {"niveles": {"$ne": None}}),
        ("consumos_atipicos", {"obraId": "x' OR 1=1"}),
        ("consumos_atipicos", {"obraId": {"$ne": None}}),
        ("consumos_atipicos", {"estados": {"$in": ["PENDIENTE"]}}),
        ("consultar_reporte", {"conjunto": "COBROS", "filtros": _filtro("obraId", "x' OR 1=1")}),
        ("consultar_reporte", {"conjunto": "CONSUMOS_ATIPICOS", "filtros": _filtro("estadoRevision", {"$ne": None})}),
    ]  # fmt: skip
    async with cliente_mcp(aplicacion) as cliente:
        for herramienta, argumentos in casos:
            error, salida = await llamar(cliente, herramienta, argumentos)
            assert (error, salida["codigo"]) == (True, "ARGUMENTO_INVALIDO"), (herramienta, argumentos)
    assert consultas == []


@pytest.mark.integracion
async def test_m5_llamar_todas_las_herramientas_no_cambia_nada(
    app: tuple[FastAPI, httpx.AsyncClient], motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]]
) -> None:
    aplicacion, _ = app
    antes = await _huella(motor, ia_db)
    async with cliente_mcp(aplicacion) as cliente:
        for herramienta in (await cliente.list_tools()).tools:
            argumentos = {"conjunto": "MOVIMIENTOS"} if herramienta.name == "consultar_reporte" else {}
            error, _ = await llamar(cliente, herramienta.name, argumentos)
            assert not error, herramienta.name
    assert await _huella(motor, ia_db) == antes


@pytest.mark.integracion
@pytest.mark.parametrize("ruta", ["/predicciones?obraIds=" + d.O1, "/consumos-atipicos", "/agentes", "/sugerencias"])
async def test_m6_token_de_agente_ia_en_rutas_rest(app: tuple[FastAPI, httpx.AsyncClient], ruta: str) -> None:
    _, http = app
    respuesta = await http.get(ruta, headers=encabezado_agente())
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (403, "SIN_PERMISO")


# --- secretos ---------------------------------------------------------------------------------------------------------


def _anthropic() -> ModeloAnthropic:
    respuestas = [
        ([{"type": "tool_use", "id": "toolu_1", "name": "buscar_diario", "input": {"obraId": O1}}], "tool_use"),
        ([{"type": "text", "text": "Listo."}], "end_turn"),
        ([{"type": "text", "text": json.dumps(reporte_seguimiento())}], "end_turn"),
    ]

    def manejar(pedido: httpx2.Request) -> httpx2.Response:
        assert pedido.headers["authorization"] == f"Bearer {CLAVE_ANTHROPIC}"
        bloques, fin = respuestas.pop(0)
        cuerpo = {"id": "m", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5", "content": bloques}
        cuerpo |= {"stop_reason": fin, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}
        return httpx2.Response(200, json=cuerpo)

    return ModeloAnthropic(CLAVE_ANTHROPIC, "claude-sonnet-5-5", transporte=httpx2.MockTransport(manejar))


@pytest.mark.integracion
async def test_k1_el_registro_no_tiene_la_clave_el_secreto_ni_el_token(
    ia_db: AsyncDatabase[dict[str, Any]], caplog: pytest.LogCaptureFixture
) -> None:
    def keycloak(pedido: httpx.Request) -> httpx.Response:
        assert SECRETO_AGENTES in pedido.content.decode()
        return httpx.Response(200, json={"access_token": TOKEN_AGENTES, "expires_in": 300})

    token = TokenAgentes(
        httpx.AsyncClient(transport=httpx.MockTransport(keycloak)), "http://kc/token", "ia-agentes", SECRETO_AGENTES
    )
    servidores = servidores_de_prueba()
    silenciar_librerias()  # como en producción (configurar_registros)
    modelo = _anthropic()
    with caplog.at_level(logging.DEBUG):
        async with entorno(ia_db, modelo, servidores=servidores) as e:
            conector = ConectorMcpReal(
                {n: s.url for n, s in servidores.items()}, token, {n: s.transporte() for n, s in servidores.items()}
            )
            e.ejecutor = EjecutorAgente(modelo, conector, e.ejecuciones, e.sugerencias, e.reloj)
            ejecucion = await e.correr("SEGUIMIENTO_OBRAS")
    assert ejecucion.estado == "TERMINADA", ejecucion.error
    assert servidores["obras"].encabezados[0]["authorization"] == f"Bearer {TOKEN_AGENTES}"
    texto = "\n".join(f"{r.name} {r.getMessage()}" for r in caplog.records)
    assert "Llamada al modelo: proveedor=anthropic" in texto
    for secreto in (CLAVE_ANTHROPIC, SECRETO_AGENTES, TOKEN_AGENTES, "Bearer"):
        assert secreto not in texto, secreto


@pytest.mark.parametrize("ejecutar", [main.ejecutar, worker.ejecutar], ids=["ia-api", "ia-worker"])
def test_k2_anthropic_sin_clave_no_arranca_sin_mostrar_otras_variables(
    ejecutar: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IA_LLM_PROVEEDOR", "anthropic")
    monkeypatch.setenv("IA_ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("IA_AGENTES_CLIENT_SECRET", SECRETO_AGENTES)
    monkeypatch.setenv("IA_REPORTES_DB_URL", "postgresql+psycopg://ia:clave-de-la-base@127.0.0.1:1/reportes_db")
    raiz = logging.getLogger()
    manejadores, nivel = list(raiz.handlers), raiz.level
    try:
        with pytest.raises(SystemExit) as salida:
            ejecutar()
    finally:
        raiz.handlers[:] = manejadores
        raiz.setLevel(nivel)
    assert salida.value.code == 1
    impreso = capsys.readouterr().out
    assert "no arranca" in impreso
    assert "IA_ANTHROPIC_API_KEY" in impreso
    for valor in (SECRETO_AGENTES, "clave-de-la-base", "anthropic\n"):
        assert valor not in impreso
