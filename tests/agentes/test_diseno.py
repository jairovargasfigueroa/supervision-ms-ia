"""Pruebas del diseño de la 502 que faltan en las demás: mínimo privilegio, argumentos, límites, servidores caídos,
sugerencias sin duplicar, el servidor MCP real de IA como herramienta y el mismo guion con los dos adaptadores
simulados a nivel HTTP."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import httpx2
import pytest
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from agentes.apoyo_agentes import (
    O1,
    entorno,
    reporte_cobros,
    reporte_materiales,
    reporte_seguimiento,
    servidores_de_prueba,
)
from apoyo import reportes_503 as d
from dobles.modelo_simulado import ModeloSimulado, llamada
from ia.agentes.aplicacion.ejecutor import EjecutorAgente
from ia.agentes.dominio.ejecucion import FALLIDA, PENDIENTE, TERMINADA
from ia.agentes.infraestructura.conector_mcp import ConectorMcpReal
from ia.comun.tiempo import RelojFijo
from ia.lenguaje.aplicacion.puerto import ModeloLenguaje
from ia.lenguaje.infraestructura.anthropic import ModeloAnthropic
from ia.lenguaje.infraestructura.ollama import ModeloOllama
from protocolo_mcp.apoyo_mcp import BASE, app_ia
from protocolo_mcp.test_cliente_servidores import token_agentes

pytestmark = pytest.mark.integracion


def _errores(ejecucion: Any) -> list[tuple[str, str | None]]:
    return [(p.herramienta, p.error) for p in ejecucion.pasos]


async def test_herramienta_no_permitida_se_rechaza_y_la_ejecucion_sigue(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    modelo = ModeloSimulado(
        json_=[reporte_seguimiento(hallazgos=[{**reporte_seguimiento()["hallazgos"][0], "evidencia": [
            {"herramienta": "avance_obra", "dato": "12,5"}]}])],
        conversacion=[[llamada("consumos_atipicos"), llamada("cobros_por_vencer", dias=15), llamada("borrar_obra")],
                      "Listo."],
    )  # fmt: skip
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("SEGUIMIENTO_OBRAS")
        llamadas = e.llamadas()
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert _errores(ejecucion)[3:] == [
        ("consumos_atipicos", "HERRAMIENTA_NO_PERMITIDA"),
        ("cobros_por_vencer", "HERRAMIENTA_NO_PERMITIDA"),
        ("borrar_obra", "HERRAMIENTA_NO_PERMITIDA"),
    ]
    assert "consumos_atipicos" not in llamadas
    assert "cobros_por_vencer" not in llamadas  # no se llamó a Finanzas
    assert not any(s == "finanzas" for s in e.servidores["finanzas"].herramientas_llamadas())


async def test_conjunto_ajeno_y_argumentos_invalidos_no_llaman_al_servidor(
    ia_db: AsyncDatabase[dict[str, Any]],
) -> None:
    modelo = ModeloSimulado(
        json_=[reporte_cobros()],
        conversacion=[[llamada("consultar_reporte", conjunto="CONSUMO_MATERIAL"),
                       llamada("planillas_estado", obraId="x' OR 1=1"),
                       llamada("planillas_estado", estados=[{"$ne": None}])], "Listo."],
    )  # fmt: skip
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("CONTROL_COBROS")
        llamadas = e.llamadas()
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert _errores(ejecucion)[2:] == [
        ("consultar_reporte", "ARGUMENTO_INVALIDO"),
        ("planillas_estado", "ARGUMENTO_INVALIDO"),
        ("planillas_estado", "ARGUMENTO_INVALIDO"),
    ]
    assert "consultar_reporte" not in llamadas
    assert llamadas.count("planillas_estado") == 1  # solo el paso inicial
    resultado = next(
        m for m in modelo.llamadas_de("conversar")[1].mensajes if m.nombre_herramienta == "consultar_reporte"
    )
    assert resultado.es_error
    assert "COBROS, PLANILLAS" in resultado.contenido


async def test_limite_de_8_llamadas(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    veinte = [llamada("buscar_diario", obraId=O1) for _ in range(20)]
    modelo = ModeloSimulado(json_=[reporte_seguimiento()], conversacion=[veinte, veinte])
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("SEGUIMIENTO_OBRAS")
        llamadas = e.llamadas()
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert len(ejecucion.pasos) == 8
    assert len(llamadas) == 8
    assert len(modelo.llamadas_de("conversar")) == 1  # con el límite alcanzado pasa al reporte
    resultados = [m for m in modelo.llamadas_de("generar_json")[0].mensajes if m.nombre_herramienta == "buscar_diario"]
    assert len(resultados) == 20  # cada pedido tiene su respuesta (las que pasan el límite, con LIMITE_ALCANZADO)
    assert sum("LIMITE_ALCANZADO" in m.contenido for m in resultados) == 15


async def test_resultado_con_is_error_queda_como_paso_y_vuelve_al_modelo(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    servidores = servidores_de_prueba()
    diario = next(h for h in servidores["obras"]._mcp._herramientas.values() if h.nombre == "buscar_diario")
    diario.error = ("OBRA_NO_ENCONTRADA", "La obra no existe.")
    hallazgo = {**reporte_seguimiento()["hallazgos"][0], "evidencia": [{"herramienta": "avance_obra", "dato": "x"}]}
    modelo = ModeloSimulado(
        json_=[reporte_seguimiento(hallazgos=[hallazgo])],
        conversacion=[[llamada("buscar_diario", obraId=O1)], "Listo."],
    )
    async with entorno(ia_db, modelo, servidores=servidores) as e:
        ejecucion = await e.correr("SEGUIMIENTO_OBRAS")
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert _errores(ejecucion)[3] == ("buscar_diario", "OBRA_NO_ENCONTRADA")
    resultado = next(
        m for m in modelo.llamadas_de("generar_json")[0].mensajes if m.nombre_herramienta == "buscar_diario"
    )
    assert resultado.es_error
    assert "OBRA_NO_ENCONTRADA" in resultado.contenido


async def test_servidor_caido_sigue_y_todos_caidos_es_sin_herramientas(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    servidores = servidores_de_prueba()
    servidores["finanzas"].caido = True
    soloplanilla = reporte_cobros()
    soloplanilla["hallazgos"] = soloplanilla["hallazgos"][1:]
    soloplanilla["sugerencias"] = []
    modelo = ModeloSimulado(json_=[soloplanilla], conversacion=["Listo."])
    async with entorno(ia_db, modelo, servidores=servidores) as e:
        ejecucion = await e.correr("CONTROL_COBROS")
        servidores["obras"].caido = True
        servidores["ia"].caido = True
        sin_nada = await e.correr("CONTROL_COBROS")
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert ejecucion.pasos[0].herramienta == "tools/list"
    assert ejecucion.pasos[0].servidor == "finanzas"
    assert (ejecucion.pasos[0].error or "").startswith("SERVIDOR_NO_DISPONIBLE")
    assert ejecucion.resumen is not None
    assert ejecucion.resumen.endswith("(Sin respuesta de: finanzas.)")
    assert sin_nada.estado == FALLIDA
    assert sin_nada.error is not None
    assert (sin_nada.error.codigo, sin_nada.error.mensaje) == ("SIN_HERRAMIENTAS", "Sin herramientas disponibles")


async def test_sugerencia_pendiente_no_se_duplica_entre_ejecuciones(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    modelo = ModeloSimulado(json_=[reporte_materiales(), reporte_materiales()], conversacion=["Listo.", "Listo."])
    async with entorno(ia_db, modelo) as e:
        primera = await e.correr("CONTROL_MATERIALES")
        segunda = await e.correr("CONTROL_MATERIALES")
        sugerencias = await e.sugerencias.listar(PENDIENTE, ["CONTROL_MATERIALES"], None, 50)
    assert (primera.estado, segunda.estado) == (TERMINADA, TERMINADA)
    assert len(sugerencias) == 2
    assert {s.ejecucion_id for s in sugerencias} == {primera.id}


async def test_herramientas_del_servidor_mcp_real_de_ia(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> None:
    """`riesgo_retraso` sin predicciones (402 sin modelo) devuelve [] y el agente sigue; `consumos_atipicos` por
    defecto trae solo los PENDIENTE de los últimos 30 días."""
    await d.cargar(motor, ia_db)
    servidores = servidores_de_prueba()
    async with app_ia(motor, url_lector, ia_db, url_mongo_pruebas) as (app, _):
        transportes: dict[str, httpx2.AsyncBaseTransport] = {n: s.transporte() for n, s in servidores.items()}
        transportes["ia"] = httpx2.ASGITransport(app=app)
        urls = {n: s.url for n, s in servidores.items()} | {"ia": f"{BASE}/mcp"}
        conector = ConectorMcpReal(urls, token_agentes(), transportes)
        materiales = {**reporte_materiales(), "hallazgos": reporte_materiales()["hallazgos"][:1]}
        materiales["sugerencias"] = materiales["sugerencias"][:1]
        modelo = ModeloSimulado(json_=[reporte_seguimiento(hallazgos=[]) | {"sugerencias": []}, materiales])
        async with entorno(ia_db, modelo, servidores=servidores) as e:
            e.ejecutor = EjecutorAgente(modelo, conector, e.ejecuciones, e.sugerencias, RelojFijo(d.AHORA))
            seguimiento = await e.correr("SEGUIMIENTO_OBRAS")
            control = await e.correr("CONTROL_MATERIALES")
    assert seguimiento.estado == TERMINADA, seguimiento.error
    riesgo = next(p for p in seguimiento.pasos if p.herramienta == "riesgo_retraso")
    assert (riesgo.servidor, riesgo.error) == ("ia", None)
    assert riesgo.resultado_resumen.startswith("0 filas")
    assert control.estado == TERMINADA, control.error
    atipicos = next(p for p in control.pasos if p.herramienta == "consumos_atipicos")
    assert atipicos.argumentos == "{}"
    assert atipicos.resultado_resumen.startswith("2 filas")
    assert "CONFIRMADO" not in atipicos.resultado_resumen


# --- el mismo guion con los dos adaptadores (HTTP simulado) ----------------------------------------------------------

GUION_REPORTE = reporte_seguimiento()


def _ollama() -> ModeloOllama:
    respuestas = [
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "buscar_diario", "arguments": {"obraId": O1, "texto": "lluvia"}}}]},
        {"role": "assistant", "content": "Tengo lo necesario."},
        {"role": "assistant", "content": json.dumps(GUION_REPORTE)},
    ]  # fmt: skip

    def manejar(_: httpx.Request) -> httpx.Response:
        cuerpo = {"model": "qwen3:8b", "created_at": "2027-04-20T10:00:00Z", "message": respuestas.pop(0), "done": True}
        return httpx.Response(200, json=cuerpo)

    return ModeloOllama("http://ollama.prueba:11434", "qwen3:8b", transporte=httpx.MockTransport(manejar))


def _anthropic() -> ModeloAnthropic:
    respuestas = [
        ([{"type": "tool_use", "id": "toolu_1", "name": "buscar_diario",
           "input": {"obraId": O1, "texto": "lluvia"}}], "tool_use"),
        ([{"type": "text", "text": "Tengo lo necesario."}], "end_turn"),
        ([{"type": "text", "text": json.dumps(GUION_REPORTE)}], "end_turn"),
    ]  # fmt: skip

    def manejar(_: httpx2.Request) -> httpx2.Response:
        bloques, fin = respuestas.pop(0)
        cuerpo = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": bloques,
            "stop_reason": fin,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        return httpx2.Response(200, json=cuerpo)

    return ModeloAnthropic("sk-ant-prueba-no-real", "claude-sonnet-5-5", transporte=httpx2.MockTransport(manejar))


@pytest.mark.parametrize("crear", [_ollama, _anthropic], ids=["ollama", "anthropic"])
async def test_mismo_guion_con_los_dos_adaptadores(
    ia_db: AsyncDatabase[dict[str, Any]], crear: Callable[[], ModeloLenguaje]
) -> None:
    modelo = crear()
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("SEGUIMIENTO_OBRAS")
        sugerencias = await e.sugerencias.listar(None, ["SEGUIMIENTO_OBRAS"], None, 50)
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert [(p.herramienta, p.argumentos) for p in ejecucion.pasos] == [
        ("avance_obra", "{}"),
        ("planillas_estado", '{"estados": ["BORRADOR", "PRESENTADA", "OBSERVADA", "APROBANDO"]}'),
        ("riesgo_retraso", '{"niveles": ["ALTO", "MEDIO"]}'),
        ("buscar_diario", '{"obraId": "b0000000-0000-4000-8000-000000000001", "texto": "lluvia"}'),
    ]
    assert [(h.obra_id, h.tipo) for h in ejecucion.hallazgos] == [(O1, "ATRASO")]
    assert [s.accion for s in sugerencias] == ["SOLICITAR_INFORME_RESIDENTE"]
