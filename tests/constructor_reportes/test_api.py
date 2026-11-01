"""Endpoints del constructor de reportes (503) con la API real, PostgreSQL (usuario lector) y MongoDB reales y
`ModeloSimulado`."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo import reportes_503 as d
from apoyo.tokens import GERENTE_ID, RESIDENTE_ID, encabezado
from constructor_reportes.apoyo_api import DIRECTORIO, MaterialesSimulados, cliente_api
from dobles.modelo_simulado import ModeloSimulado
from ia.comun.tiempo import RelojFijo
from ia.lenguaje.aplicacion.puerto import LlmNoDisponible, LlmTiempoAgotado

pytestmark = pytest.mark.integracion

GERENTE = encabezado(["gerente"], sub=GERENTE_ID)
RECETA_CEMENTO = {
    "conjunto": "CONSUMO_MATERIAL",
    "filtros": [
        {"campo": "obra", "operador": "CONTIENE", "valor": "Las Palmas"},
        {"campo": "material", "operador": "CONTIENE", "valor": "cemento"},
        {"campo": "periodo", "operador": "DESDE", "valor": "2027-01"},
        {"campo": "periodo", "operador": "HASTA", "valor": "2027-03"},
    ],
}


@pytest.fixture
async def datos(motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    await d.cargar(motor, ia_db)


@pytest.fixture
def modelo() -> ModeloSimulado:
    return ModeloSimulado()


@pytest.fixture
def materiales() -> MaterialesSimulados:
    return MaterialesSimulados()


@pytest.fixture
async def api(
    datos: None,
    motor: AsyncEngine,
    url_lector: str,
    ia_db: AsyncDatabase[dict[str, Any]],
    url_mongo_pruebas: str,
    modelo: ModeloSimulado,
    materiales: MaterialesSimulados,
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    async with cliente_api(motor, url_lector, ia_db, url_mongo_pruebas, modelo, materiales) as (http, app):
        yield http, app


async def _consultas(ia_db: AsyncDatabase[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c async for c in ia_db["consulta_reporte"].find({}).sort("creada_en", 1)]


async def test_texto_a_reporte_con_resumen_tabla_y_nombres(
    api: tuple[httpx.AsyncClient, FastAPI], modelo: ModeloSimulado, ia_db: AsyncDatabase[dict[str, Any]]
) -> None:
    http, _ = api
    modelo._json = [RECETA_CEMENTO, {"resumen": "En Edificio Las Palmas se usaron 1740 bolsas de cemento."}]
    texto = "¿Cuánto cemento se usó en Las Palmas este trimestre?"
    respuesta = await http.post("/consultas-reporte", json={"texto": texto, "directorio": DIRECTORIO}, headers=GERENTE)
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["conjunto"] == "CONSUMO_MATERIAL" and cuerpo["tituloConjunto"] == "Material usado"
    assert cuerpo["texto"] == texto and cuerpo["atajo"] is None
    assert cuerpo["aplicado"][:3] == [
        {"nombre": "Obra", "valor": "contiene «Las Palmas»"},
        {"nombre": "Material", "valor": "contiene «cemento»"},
        {"nombre": "Periodo", "valor": "2027-01 a 2027-03"},
    ]
    assert [c["clave"] for c in cuerpo["columnas"]] == [
        "obra",
        "material",
        "unidad",
        "suma_cantidad",
        "suma_monto",
        "suma_salidas",
    ]
    assert cuerpo["filas"] == [["Edificio Las Palmas", "Cemento IP-30", "bolsa", "1740.000", "107880.00", "41"]]
    assert cuerpo["resumen"] == "En Edificio Las Palmas se usaron 1740 bolsas de cemento."
    assert cuerpo["resumenPlantilla"] is False
    assert (cuerpo["totalFilas"], cuerpo["truncado"], cuerpo["avisos"]) == (1, False, [])
    assert cuerpo["receta"]["agruparPor"] == ["obra", "material"] and cuerpo["receta"]["limite"] == 100
    assert cuerpo["creadaEn"] == "2027-03-20T14:00:00Z"
    guardada = (await _consultas(ia_db))[0]
    assert guardada["_id"] == cuerpo["id"]
    assert (guardada["usuario_id"], guardada["resultado"], guardada["total_filas"]) == (GERENTE_ID, "OK", 1)
    assert guardada["receta"] == cuerpo["receta"] and guardada["proveedor_llm"] == "ollama"


async def test_atajo_no_llama_al_modelo_para_interpretar(
    api: tuple[httpx.AsyncClient, FastAPI], modelo: ModeloSimulado
) -> None:
    http, _ = api
    modelo._json = [LlmNoDisponible("caído")]  # solo el resumen intenta usarlo
    respuesta = await http.post(
        "/consultas-reporte", json={"atajo": "COBROS_VENCIDOS", "directorio": DIRECTORIO}, headers=GERENTE
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["atajo"] == "COBROS_VENCIDOS"
    assert cuerpo["filas"] == [
        ["COB-2027-0001", "Edificio Las Palmas", "Constructora Andina SRL", "20000.00", "2027-02-09"]
    ]
    assert cuerpo["resumenPlantilla"] is True
    assert cuerpo["resumen"].startswith("Se encontraron 1 filas para «Cobros vencidos»")
    assert len(modelo.llamadas_de("generar_json")) == 1  # el resumen, no la interpretación


async def test_errores_del_cuerpo(
    api: tuple[httpx.AsyncClient, FastAPI], modelo: ModeloSimulado, ia_db: AsyncDatabase[dict[str, Any]]
) -> None:
    http, _ = api
    casos = [
        ({}, 400, "DATOS_INVALIDOS"),
        ({"texto": "obras", "atajo": "COBROS_VENCIDOS"}, 400, "DATOS_INVALIDOS"),
        ({"atajo": "BORRAR_TODO"}, 400, "DATOS_INVALIDOS"),
        ({"texto": "obras", "directorio": {"clientes": "no"}}, 400, "DATOS_INVALIDOS"),
        ({"texto": "ab"}, 400, "TEXTO_INVALIDO"),
        ({"texto": "x" * 5000}, 400, "TEXTO_INVALIDO"),
    ]
    for cuerpo, estado, codigo in casos:
        respuesta = await http.post("/consultas-reporte", json=cuerpo, headers=GERENTE)
        assert (respuesta.status_code, respuesta.json()["codigo"]) == (estado, codigo), cuerpo
    no_json = await http.post("/consultas-reporte", content=b"no es json", headers=GERENTE)
    assert (no_json.status_code, no_json.json()["codigo"]) == (400, "DATOS_INVALIDOS")
    assert modelo.llamadas == []
    assert await _consultas(ia_db) == []  # los 400 no se guardan


async def test_no_reconocida_e_invalida_se_guardan(
    api: tuple[httpx.AsyncClient, FastAPI], modelo: ModeloSimulado, ia_db: AsyncDatabase[dict[str, Any]]
) -> None:
    http, _ = api
    modelo._json = [
        {"conjunto": "NINGUNO"},
        {"conjunto": "CONSUMO_MATERIAL", "filtros": [{"campo": "estado", "operador": "IGUAL", "valor": "X"}]},
    ]
    no = await http.post("/consultas-reporte", json={"texto": "¿Qué hora es?"}, headers=GERENTE)
    assert (no.status_code, no.json()["codigo"]) == (422, "INTENCION_NO_RECONOCIDA")
    invalida = await http.post("/consultas-reporte", json={"texto": "material por estado"}, headers=GERENTE)
    assert (invalida.status_code, invalida.json()["codigo"]) == (422, "RECETA_INVALIDA")
    assert invalida.json()["errores"] == [
        {"campo": "filtros[0].campo", "mensaje": "«estado» no es un filtro de CONSUMO_MATERIAL."}
    ]
    guardadas = await _consultas(ia_db)
    assert [(g["resultado"], g["conjunto"], g["receta"]) for g in guardadas] == [
        ("NO_RECONOCIDA", None, None),
        ("RECETA_INVALIDA", None, None),
    ]


@pytest.mark.parametrize("fallo", [LlmNoDisponible("caído"), LlmTiempoAgotado("lento")])
async def test_llm_no_disponible_al_interpretar(
    api: tuple[httpx.AsyncClient, FastAPI],
    modelo: ModeloSimulado,
    ia_db: AsyncDatabase[dict[str, Any]],
    fallo: Exception,
) -> None:
    http, _ = api
    modelo._json = [fallo]
    respuesta = await http.post("/consultas-reporte", json={"texto": "obras atrasadas"}, headers=GERENTE)
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (503, "LLM_NO_DISPONIBLE")
    assert [(g["resultado"], g["error_codigo"]) for g in await _consultas(ia_db)] == [("ERROR", "LLM_NO_DISPONIBLE")]


async def test_servicio_no_disponible_por_materiales_o_directorio(
    api: tuple[httpx.AsyncClient, FastAPI], modelo: ModeloSimulado, materiales: MaterialesSimulados
) -> None:
    http, _ = api
    materiales.caido = True
    modelo._json = [RECETA_CEMENTO]
    respuesta = await http.post("/consultas-reporte", json={"texto": "cemento en Las Palmas"}, headers=GERENTE)
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (503, "SERVICIO_NO_DISPONIBLE")
    modelo._json = [
        {"conjunto": "COBROS", "filtros": [{"campo": "cliente", "operador": "CONTIENE", "valor": "Andina"}]}
    ]
    respuesta = await http.post("/consultas-reporte", json={"texto": "cobros de Andina"}, headers=GERENTE)
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (503, "SERVICIO_NO_DISPONIBLE")
    # Sin filtro: el id y el aviso.
    modelo._json = [{"conjunto": "COBROS", "columnas": ["numero", "cliente"]}, {"resumen": "Hay 3 cobros."}]
    respuesta = await http.post("/consultas-reporte", json={"texto": "cobros"}, headers=GERENTE)
    assert respuesta.status_code == 200
    assert respuesta.json()["avisos"] == ["Nombres de clientes no disponibles"]
    assert respuesta.json()["filas"][0] == ["COB-2027-0003", d.C2]


async def test_tiempo_agotado(api: tuple[httpx.AsyncClient, FastAPI], ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    http, app = api
    import ia.constructor_reportes.aplicacion.consultar as consultar

    original = consultar.TIEMPO_MAXIMO_S
    app.state.modelo_lenguaje = ModeloSimulado(json_=[{"conjunto": "OBRAS"}], demora_s=1)
    consultar.ConsultarReporte.__init__.__defaults__ = (0.05,)
    try:
        respuesta = await http.post("/consultas-reporte", json={"texto": "obras"}, headers=GERENTE)
    finally:
        consultar.ConsultarReporte.__init__.__defaults__ = (original,)
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (504, "TIEMPO_AGOTADO")
    assert [(g["resultado"], g["error_codigo"]) for g in await _consultas(ia_db)] == [("ERROR", "TIEMPO_AGOTADO")]


async def test_limite_de_10_por_minuto_tambien_con_atajos(api: tuple[httpx.AsyncClient, FastAPI]) -> None:
    http, _ = api
    for _ in range(10):
        respuesta = await http.post("/consultas-reporte", json={"atajo": "AVANCE_OBRAS"}, headers=GERENTE)
        assert respuesta.status_code == 200
    respuesta = await http.post("/consultas-reporte", json={}, headers=GERENTE)  # el límite va antes de validar
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (429, "DEMASIADAS_CONSULTAS")
    assert respuesta.headers["retry-after"] == "60"
    otro = await http.post("/consultas-reporte", json={"atajo": "AVANCE_OBRAS"}, headers=encabezado(["admin"]))
    assert otro.status_code == 200


async def test_historial_y_catalogo(api: tuple[httpx.AsyncClient, FastAPI]) -> None:
    http, app = api
    for segundos, atajo in enumerate(("AVANCE_OBRAS", "COBROS_VENCIDOS", "PLANILLAS_OBSERVADAS")):
        # Un instante distinto por consulta: con el mismo instante, el orden entre ellas no está definido.
        app.state.reloj = RelojFijo(d.AHORA + timedelta(seconds=segundos))
        await http.post("/consultas-reporte", json={"atajo": atajo}, headers=GERENTE)
    historial = await http.get("/consultas-reporte", params={"limite": 2}, headers=GERENTE)
    assert historial.status_code == 200
    assert [h["atajo"] for h in historial.json()] == ["PLANILLAS_OBSERVADAS", "COBROS_VENCIDOS"]
    assert set(historial.json()[0]) == {
        "id",
        "texto",
        "atajo",
        "conjunto",
        "resultado",
        "resumen",
        "totalFilas",
        "creadaEn",
    }
    assert (await http.get("/consultas-reporte", headers=encabezado(["admin"]))).json() == []
    assert (await http.get("/consultas-reporte", params={"limite": 21}, headers=GERENTE)).status_code == 400

    catalogo = (await http.get("/catalogo-reportes", headers=GERENTE)).json()
    assert [c["conjunto"] for c in catalogo["conjuntos"]] == [
        "OBRAS",
        "CONSUMO_MATERIAL",
        "MOVIMIENTOS",
        "COBROS",
        "PLANILLAS",
        "CONSUMOS_ATIPICOS",
    ]
    material = catalogo["conjuntos"][1]
    assert material["filtros"] == ["obra", "zona", "cliente", "material", "materialId", "periodo"]
    assert material["agrupaciones"] == ["obra", "zona", "cliente", "material", "periodo"]
    assert material["ejemplos"][0] == "¿Cuánto cemento se usó en Las Palmas este trimestre?"
    assert len(catalogo["atajos"]) == 10
    assert catalogo["atajos"][5] == {
        "atajo": "COBROS_POR_VENCER",
        "titulo": "Cobros que vencen en los próximos 30 días",
        "descripcion": "Cobros pendientes que vencen entre hoy y dentro de 30 días.",
    }


async def test_roles(api: tuple[httpx.AsyncClient, FastAPI]) -> None:
    http, _ = api
    residente = encabezado(["residente"], sub=RESIDENTE_ID)
    for metodo, ruta in (("POST", "/consultas-reporte"), ("GET", "/consultas-reporte"), ("GET", "/catalogo-reportes")):
        assert (await http.request(metodo, ruta, json={"atajo": "AVANCE_OBRAS"}, headers=residente)).status_code == 403
        assert (await http.request(metodo, ruta)).status_code == 401
        agente = encabezado(["agente_ia"], azp="ia-agentes")
        assert (await http.request(metodo, ruta, json={"atajo": "AVANCE_OBRAS"}, headers=agente)).status_code == 403
