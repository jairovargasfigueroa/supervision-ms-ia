"""Catálogo del host (501): herramienta → servidor, comparación con `tools/list` y validación con el contrato."""

import logging
from typing import Any

import pytest
from pydantic import ValidationError
from starlette.datastructures import State

from ia.config import ConfiguracionWorker
from ia.mcp.cliente.catalogo import CONTRATO, SERVIDORES, aceptadas, servidor_de, urls_servidores
from ia.mcp.cliente.cliente import HerramientaPublicada
from ia.mcp.servidor.herramientas import herramientas_ia
from ia.mcp.servidor.servidor import ServidorMcp


def _publicada(nombre: str, esquema: dict[str, Any]) -> HerramientaPublicada:
    return HerramientaPublicada(nombre, "Devuelve…", esquema)


def _esquema(nombre: str) -> dict[str, Any]:
    return CONTRATO[nombre].entrada.model_json_schema(by_alias=True)


def test_las_nueve_herramientas_en_los_cuatro_servidores() -> None:
    assert {n: c.servidor for n, c in CONTRATO.items()} == {
        "avance_obra": "obras",
        "buscar_diario": "obras",
        "planillas_estado": "obras",
        "stock_obra": "materiales",
        "consumos_partida": "materiales",
        "cobros_por_vencer": "finanzas",
        "consultar_reporte": "ia",
        "riesgo_retraso": "ia",
        "consumos_atipicos": "ia",
    }
    assert "asistencia_obra" not in CONTRATO
    assert servidor_de("stock_obra") == "materiales"
    assert servidor_de("borrar_obra") is None
    assert set(SERVIDORES) == {"obras", "materiales", "finanzas", "ia"}


def test_urls_de_los_cuatro_servidores() -> None:
    config = ConfiguracionWorker(propio_mcp_url="http://ia-api:8085/mcp")
    assert urls_servidores(config) == {
        "obras": "http://localhost:8081/mcp",
        "materiales": "http://localhost:8082/mcp",
        "finanzas": "http://localhost:8083/mcp",
        "ia": "http://ia-api:8085/mcp",
    }


async def test_lo_que_publica_el_servidor_de_ia_cumple_el_contrato() -> None:
    publicadas = [_publicada(h.name, h.input_schema) for h in await ServidorMcp(herramientas_ia(State())).list_tools()]
    assert [h.nombre for h in aceptadas("ia", publicadas)] == [h.nombre for h in publicadas]


def test_esquemas_de_obras_materiales_y_finanzas_segun_el_diseno() -> None:
    propiedades = {n: (set(c.propiedades), set(c.obligatorias)) for n, c in CONTRATO.items() if c.servidor != "ia"}
    assert propiedades == {
        "avance_obra": ({"obraId", "periodo"}, set()),
        "buscar_diario": ({"obraId", "desde", "hasta", "texto", "limite"}, {"obraId"}),
        "planillas_estado": ({"obraId", "estados", "desdePeriodo", "limite"}, set()),
        "stock_obra": ({"obraId", "estados", "limite"}, set()),
        "consumos_partida": ({"obraId", "periodo", "partidaId"}, {"obraId", "periodo"}),
        "cobros_por_vencer": ({"dias", "incluirVencidos", "obraId"}, set()),
    }


def test_desconocida_de_otro_servidor_o_con_otro_esquema_se_ignora(caplog: pytest.LogCaptureFixture) -> None:
    otro_esquema = {**_esquema("stock_obra"), "properties": {**_esquema("stock_obra")["properties"], "borrar": {}}}
    publicadas = [
        _publicada("stock_obra", _esquema("stock_obra")),
        _publicada("consumos_partida", _esquema("consumos_partida")),
        _publicada("ajustar_stock", {"type": "object", "properties": {}}),
        _publicada("cobros_por_vencer", _esquema("cobros_por_vencer")),  # es de Finanzas
        _publicada("stock_obra", otro_esquema),
        _publicada("consumos_partida", {**_esquema("consumos_partida"), "required": ["obraId"]}),
    ]
    with caplog.at_level(logging.WARNING, logger="ia.mcp.cliente"):
        validas = aceptadas("materiales", publicadas)
    assert [h.nombre for h in validas] == ["stock_obra", "consumos_partida"]
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 4


@pytest.mark.parametrize(
    ("herramienta", "argumentos"),
    [
        ("avance_obra", {"obraId": "x' OR 1=1"}),
        ("avance_obra", {"periodo": "2027-13"}),
        ("buscar_diario", {}),
        ("buscar_diario", {"obraId": "b0000000-0000-4000-8000-000000000001", "limite": 1000}),
        ("planillas_estado", {"estados": ["BORRADA"]}),
        ("stock_obra", {"obraId": {"$ne": None}}),
        ("consumos_partida", {"obraId": "x", "periodo": "2027-03", "extra": 1}),
        ("cobros_por_vencer", {"incluirVencidos": "si"}),
    ],
)
def test_el_contrato_rechaza_argumentos_invalidos(herramienta: str, argumentos: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CONTRATO[herramienta].entrada.model_validate(argumentos)


def test_el_contrato_acepta_argumentos_validos() -> None:
    obra = "b0000000-0000-4000-8000-000000000001"
    CONTRATO["avance_obra"].entrada.model_validate({"obraId": obra, "periodo": "2027-03"})
    CONTRATO["buscar_diario"].entrada.model_validate({"obraId": obra, "desde": "2027-03-01", "texto": "lluvia"})
    CONTRATO["planillas_estado"].entrada.model_validate({"estados": ["PRESENTADA", "OBSERVADA"]})
    CONTRATO["stock_obra"].entrada.model_validate({"estados": ["BAJO", "JUSTO"]})
    CONTRATO["cobros_por_vencer"].entrada.model_validate({"dias": 15, "incluirVencidos": True})
