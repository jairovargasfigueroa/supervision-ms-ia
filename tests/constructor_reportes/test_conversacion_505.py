"""505: conversación en `POST /consultas-reporte` (API real, PostgreSQL y MongoDB reales, `ModeloSimulado`).

Preguntas del modelo y del código, respuesta a una duda sin modelo, seguimientos sobre la última receta, tope de 5
preguntas, conversación de otro usuario o expirada, autocorrección y caché de recetas."""

from collections.abc import AsyncIterator, Mapping
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo import reportes_503 as d
from apoyo.tokens import GERENTE_ID, encabezado
from constructor_reportes.apoyo_api import DIRECTORIO, MATERIALES, MaterialesSimulados, cliente_api
from dobles.modelo_simulado import ModeloSimulado
from ia.comun.tiempo import RelojFijo
from ia.constructor_reportes.aplicacion.interpretar import EJEMPLOS, HOY_DE_LOS_EJEMPLOS
from ia.constructor_reportes.aplicacion.puertos import MaterialCatalogo
from ia.constructor_reportes.dominio.catalogo import REST
from ia.constructor_reportes.dominio.validacion import validar_receta

pytestmark = pytest.mark.integracion

GERENTE = encabezado(["gerente"], sub=GERENTE_ID)
CEMENTO_COLA = "e0000000-0000-4000-8000-000000000009"


class MaterialesConDosCementos(MaterialesSimulados):
    async def materiales(self, token: str) -> Mapping[str, MaterialCatalogo]:
        return {**MATERIALES, CEMENTO_COLA: MaterialCatalogo(CEMENTO_COLA, "CEM-COL", "Cemento cola", "bolsa")}


@pytest.fixture
async def api(
    motor: AsyncEngine,
    url_lector: str,
    ia_db: AsyncDatabase[dict[str, Any]],
    url_mongo_pruebas: str,
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI, ModeloSimulado]]:
    await d.cargar(motor, ia_db)
    modelo = ModeloSimulado()
    async with cliente_api(motor, url_lector, ia_db, url_mongo_pruebas, modelo, MaterialesConDosCementos()) as (
        http,
        app,
    ):
        yield http, app, modelo


async def _pedir(
    http: httpx.AsyncClient,
    texto: str | None = None,
    conversacion: str | None = None,
    *,
    atajo: str | None = None,
    usuario: dict[str, str] = GERENTE,
) -> httpx.Response:
    cuerpo: dict[str, Any] = {"directorio": DIRECTORIO}
    if texto is not None:
        cuerpo["texto"] = texto
    if atajo is not None:
        cuerpo["atajo"] = atajo
    if conversacion is not None:
        cuerpo["conversacionId"] = conversacion
    return await http.post("/consultas-reporte", json=cuerpo, headers=usuario)


def _receta(**receta: Any) -> dict[str, Any]:
    return {"accion": "RECETA", "receta": receta}


def _interpretaciones(modelo: ModeloSimulado) -> list[Any]:
    """Llamadas de `generar_json` del intérprete (las del resumen piden el esquema de `ResumenModelo`)."""
    return [ll for ll in modelo.llamadas_de("generar_json") if ll.esquema and "accion" in ll.esquema["properties"]]


async def test_pregunta_del_modelo_y_respuesta_combinada(
    api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado],
) -> None:
    http, _, modelo = api
    modelo._json = [
        {"accion": "ACLARAR", "pregunta": "¿Sobre qué quieres el reporte?", "opciones": ["Obras", "Cobros"]},
        _receta(conjunto="OBRAS"),
        {"resumen": "Dos obras en ejecución."},
    ]
    primera = (await _pedir(http, "Muéstrame lo importante")).json()
    assert primera["tipo"] == "ACLARACION" and primera["consulta"] is None
    assert primera["aclaracion"] == {
        "pregunta": "¿Sobre qué quieres el reporte?",
        "opciones": ["Obras", "Cobros"],
        "preguntasRestantes": 4,
    }
    segunda = (await _pedir(http, "Obras", primera["conversacionId"])).json()
    assert segunda["tipo"] == "REPORTE" and segunda["conversacionId"] == primera["conversacionId"]
    assert segunda["consulta"]["conjunto"] == "OBRAS" and segunda["consulta"]["totalFilas"] == 2
    # El modelo recibió la pregunta anterior en los turnos de la conversación
    usuario = _interpretaciones(modelo)[1].mensajes[1].contenido
    assert "¿Sobre qué quieres el reporte?" in usuario and '"pedido": "Obras"' in usuario


async def test_seguimiento_ajusta_la_ultima_receta(api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado]) -> None:
    http, _, modelo = api
    modelo._json = [
        _receta(conjunto="COBROS", filtros=[{"campo": "estado", "operador": "IGUAL", "valor": "VENCIDO"}]),
        {"resumen": "Un cobro vencido."},
        _receta(conjunto="COBROS", agruparPor=["obra"], metricas=[{"funcion": "SUMA", "campo": "monto"}]),
        {"resumen": "Por obra."},
    ]
    primera = (await _pedir(http, "Cobros vencidos")).json()
    segunda = (await _pedir(http, "¿y ahora por obra?", primera["conversacionId"])).json()
    assert segunda["tipo"] == "REPORTE"
    usuario = _interpretaciones(modelo)[1].mensajes[1].contenido
    assert '"recetaAnterior": {"conjunto": "COBROS"' in usuario  # el modelo recibió la receta a ajustar


async def test_duda_del_codigo_con_varios_materiales_y_opcion_sin_modelo(
    api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado],
) -> None:
    http, _, modelo = api
    # El material no está en la salida (agrupado por obra): sumar dos cementos distintos sería un error → pregunta
    receta = _receta(
        conjunto="CONSUMO_MATERIAL",
        filtros=[{"campo": "material", "operador": "CONTIENE", "valor": "cemento"}],
        agruparPor=["obra"],
    )
    modelo._json = [receta, {"resumen": "Movimientos del cemento IP-30."}]
    primera = (await _pedir(http, "¿Cuánto cemento se usó por obra?")).json()
    assert primera["tipo"] == "ACLARACION"
    assert primera["aclaracion"]["opciones"] == ["Cemento IP-30", "Cemento cola", "Todos"]
    assert "coincide con varios materiales" in primera["aclaracion"]["pregunta"]
    segunda = (await _pedir(http, "cemento ip-30", primera["conversacionId"])).json()
    assert segunda["tipo"] == "REPORTE"
    filtros = segunda["consulta"]["aplicado"]
    assert {"nombre": "Material", "valor": "contiene «Cemento IP-30»"} in filtros
    assert len(_interpretaciones(modelo)) == 1  # la opción se resolvió sin el modelo


async def test_todos_acepta_el_termino_y_no_vuelve_a_preguntar(
    api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado],
) -> None:
    http, _, modelo = api
    # El material no está en la salida (agrupado por obra): sumar dos cementos distintos sería un error → pregunta
    receta = _receta(
        conjunto="CONSUMO_MATERIAL",
        filtros=[{"campo": "material", "operador": "CONTIENE", "valor": "cemento"}],
        agruparPor=["obra"],
    )
    modelo._json = [receta, {"resumen": "Todos los cementos."}]
    primera = (await _pedir(http, "Cemento usado por obra")).json()
    assert primera["tipo"] == "ACLARACION"
    segunda = (await _pedir(http, "Todos", primera["conversacionId"])).json()
    assert segunda["tipo"] == "REPORTE"
    assert len(_interpretaciones(modelo)) == 1


async def test_nombre_inexistente_pregunta_por_parecidos(
    api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado],
) -> None:
    http, _, modelo = api
    modelo._json = [
        _receta(
            conjunto="CONSUMO_MATERIAL",
            filtros=[{"campo": "material", "operador": "CONTIENE", "valor": "fierrro"}],
            agruparPor=["obra"],
        )
    ]
    respuesta = (await _pedir(http, "Salidas de fierrro")).json()
    assert respuesta["tipo"] == "ACLARACION"
    assert respuesta["aclaracion"]["opciones"] == ["Fierro de 12 mm", "Ninguno"]
    assert respuesta["aclaracion"]["pregunta"].startswith("No encontré el material «fierrro»")


async def test_tope_de_5_preguntas(api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado]) -> None:
    http, _, modelo = api
    pregunta = {"accion": "ACLARAR", "pregunta": "¿De qué?", "opciones": []}
    modelo._json = [pregunta] * 5 + [_receta(conjunto="OBRAS"), {"resumen": "Obras."}]
    conversacion = None
    for restantes in (4, 3, 2, 1, 0):
        r = (await _pedir(http, "algo vago", conversacion)).json()
        assert (r["tipo"], r["aclaracion"]["preguntasRestantes"]) == ("ACLARACION", restantes)
        conversacion = r["conversacionId"]
    final = (await _pedir(http, "lo que sea", conversacion)).json()
    assert final["tipo"] == "REPORTE"
    # Sin preguntas restantes, el esquema ya no ofrece ACLARAR
    ultimo = _interpretaciones(modelo)[-1]
    assert ultimo.esquema["properties"]["accion"]["enum"] == ["RECETA", "NINGUNO"]


async def test_conversacion_de_otro_usuario_o_expirada_es_404(
    api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado],
) -> None:
    http, app, modelo = api
    modelo._json = [{"accion": "ACLARAR", "pregunta": "¿De qué?", "opciones": []}]
    conversacion = (await _pedir(http, "algo vago")).json()["conversacionId"]
    otro = encabezado(["admin"], sub="a0000000-0000-4000-8000-000000000001")
    respuesta = await _pedir(http, "Obras", conversacion, usuario=otro)
    assert (respuesta.status_code, respuesta.json()["codigo"]) == (404, "CONVERSACION_NO_ENCONTRADA")
    inexistente = await _pedir(http, "Obras", "c0000000-0000-4000-8000-00000000dead")
    assert inexistente.status_code == 404
    app.state.reloj = RelojFijo(d.AHORA + timedelta(minutes=31))
    expirada = await _pedir(http, "Obras", conversacion)
    assert (expirada.status_code, expirada.json()["codigo"]) == (404, "CONVERSACION_NO_ENCONTRADA")


async def test_respuesta_corta_solo_con_conversacion(api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado]) -> None:
    http, _, modelo = api
    corta = await _pedir(http, "sí")
    assert (corta.status_code, corta.json()["codigo"]) == (400, "TEXTO_INVALIDO")
    modelo._json = [{"accion": "ACLARAR", "pregunta": "¿Las obras?", "opciones": []}, _receta(conjunto="OBRAS")]
    conversacion = (await _pedir(http, "algo vago")).json()["conversacionId"]
    assert (await _pedir(http, "sí", conversacion)).status_code == 200


async def test_autocorreccion_con_los_errores_de_la_receta(
    api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado],
) -> None:
    http, _, modelo = api
    invalida = _receta(conjunto="CONSUMO_MATERIAL", filtros=[{"campo": "estado", "operador": "IGUAL", "valor": "X"}])
    modelo._json = [invalida, _receta(conjunto="OBRAS"), {"resumen": "Obras."}]
    respuesta = (await _pedir(http, "obras atrasadas")).json()
    assert respuesta["tipo"] == "REPORTE" and respuesta["consulta"]["conjunto"] == "OBRAS"
    segundo = _interpretaciones(modelo)[1].mensajes
    assert segundo[-1].contenido.startswith("Tu respuesta no es válida:")
    assert "«estado» no es un filtro de CONSUMO_MATERIAL" in segundo[-1].contenido


async def test_cache_de_recetas_no_llama_al_modelo_para_interpretar(
    api: tuple[httpx.AsyncClient, FastAPI, ModeloSimulado],
) -> None:
    http, _, modelo = api
    modelo._json = [_receta(conjunto="OBRAS"), {"resumen": "Obras."}, {"resumen": "Obras otra vez."}]
    assert (await _pedir(http, "Avance de las obras")).json()["tipo"] == "REPORTE"
    assert (await _pedir(http, "  avance de las OBRAS ")).json()["tipo"] == "REPORTE"
    assert len(_interpretaciones(modelo)) == 1


def test_cada_ejemplo_de_las_instrucciones_valida() -> None:
    for pedido, respuesta in EJEMPLOS:
        if respuesta["accion"] == "RECETA":
            validar_receta(respuesta["receta"], REST, HOY_DE_LOS_EJEMPLOS)  # no lanza
        else:
            assert respuesta["accion"] in ("ACLARAR", "NINGUNO"), pedido
