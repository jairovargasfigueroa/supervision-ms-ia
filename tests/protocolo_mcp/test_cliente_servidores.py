"""Cliente MCP del host (501) de punta a punta: contra el servidor MCP real de `ia-api` (por ASGI) y contra un
servidor de prueba del mismo SDK que publica una herramienta fuera del contrato."""

import logging
from typing import Any

import httpx
import httpx2
import pytest
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo import reportes_503 as d
from apoyo.tokens import token as firmar_token
from dobles.servidor_mcp import HerramientaPrueba, ServidorPrueba, esquema_objeto
from ia.mcp.cliente.catalogo import CONTRATO, aceptadas
from ia.mcp.cliente.cliente import ClienteMcp, FalloMcp
from ia.mcp.cliente.token import TokenAgentes
from protocolo_mcp.apoyo_mcp import AGENTE_ID, BASE, app_ia
from protocolo_mcp.test_herramientas_ia import guardar_predicciones
from protocolo_mcp.test_token_agentes import URL

EJECUCION = "c0000000-0000-4000-8000-000000000002"


def token_agentes(roles: list[str] | None = None) -> TokenAgentes:
    """Keycloak simulado que entrega tokens firmados con la clave de las pruebas (los acepta el validador)."""

    def keycloak(_: httpx.Request) -> httpx.Response:
        firmado = firmar_token(roles or ["agente_ia"], sub=AGENTE_ID, azp="ia-agentes")
        return httpx.Response(200, json={"access_token": firmado, "expires_in": 300})

    return TokenAgentes(httpx.AsyncClient(transport=httpx.MockTransport(keycloak)), URL, "ia-agentes", "s")


@pytest.mark.integracion
async def test_camino_feliz_contra_el_servidor_de_ia(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> None:
    await d.cargar(motor, ia_db)
    await guardar_predicciones(ia_db)
    async with app_ia(motor, url_lector, ia_db, url_mongo_pruebas) as (app, _):
        cliente = ClienteMcp("ia", f"{BASE}/mcp", token_agentes(), EJECUCION, transporte=httpx2.ASGITransport(app=app))
        publicadas = await cliente.listar_herramientas()
        assert [h.nombre for h in aceptadas("ia", publicadas)] == [
            "consultar_reporte",
            "riesgo_retraso",
            "consumos_atipicos",
        ]
        riesgo = await cliente.llamar("riesgo_retraso", {"niveles": ["ALTO"]})
        assert not riesgo.es_error
        assert [p["obraId"] for p in riesgo.datos["predicciones"]] == [d.O1]
        invalido = await cliente.llamar("consultar_reporte", {"conjunto": "OBRAS", "orden": [{"por": "inventado"}]})
        assert (invalido.es_error, invalido.codigo) == (True, "ARGUMENTO_INVALIDO")

        persona = ClienteMcp(
            "ia", f"{BASE}/mcp", token_agentes(["gerente"]), EJECUCION, transporte=httpx2.ASGITransport(app=app)
        )
        with pytest.raises(FalloMcp):
            await persona.listar_herramientas()


async def test_herramienta_fuera_del_contrato_se_ignora(caplog: pytest.LogCaptureFixture) -> None:
    servidor = ServidorPrueba(
        "finanzas",
        [
            HerramientaPrueba(
                "cobros_por_vencer", CONTRATO["cobros_por_vencer"].entrada.model_json_schema(by_alias=True)
            ),
            HerramientaPrueba("registrar_pago", esquema_objeto({"cobroId": {"type": "string"}}, ["cobroId"])),
        ],
    )
    async with servidor.en_marcha():
        cliente = ClienteMcp("finanzas", servidor.url, token_agentes(), EJECUCION, transporte=servidor.transporte())
        with caplog.at_level(logging.WARNING, logger="ia.mcp.cliente"):
            validas = aceptadas("finanzas", await cliente.listar_herramientas())
    assert [h.nombre for h in validas] == ["cobros_por_vencer"]
    assert any("registrar_pago" in r.getMessage() for r in caplog.records)
