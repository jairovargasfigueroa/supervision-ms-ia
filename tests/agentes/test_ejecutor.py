"""Ejecutor de los agentes (502) con `ModeloSimulado`, servidores MCP de prueba y MongoDB real."""

from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from agentes.apoyo_agentes import (
    NOMBRE_O1,
    O1,
    TEXTO_DIARIO,
    entorno,
    reporte_cobros,
    reporte_materiales,
    reporte_seguimiento,
)
from dobles.modelo_simulado import ModeloSimulado, llamada
from ia.agentes.aplicacion.ejecutor import Limites
from ia.agentes.dominio.ejecucion import FALLIDA, PENDIENTE, TERMINADA
from ia.lenguaje.aplicacion.puerto import LlmNoDisponible
from ia.lenguaje.dominio.mensajes import Rol

pytestmark = pytest.mark.integracion


async def test_camino_feliz_seguimiento_de_obras(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    modelo = ModeloSimulado(
        json_=[reporte_seguimiento()],
        conversacion=[[llamada("buscar_diario", obraId=O1, texto="lluvia")], "Tengo lo necesario."],
    )
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("SEGUIMIENTO_OBRAS")
        sugerencias = await e.sugerencias.listar(None, ["SEGUIMIENTO_OBRAS"], None, 50)
        llamadas = e.llamadas()
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert [p.herramienta for p in ejecucion.pasos] == [
        "avance_obra",
        "planillas_estado",
        "riesgo_retraso",
        "buscar_diario",
    ]
    assert [p.servidor for p in ejecucion.pasos] == ["obras", "obras", "ia", "obras"]
    assert all(p.error is None for p in ejecucion.pasos)
    assert ejecucion.pasos[0].resultado_resumen.startswith("2 filas: ")
    assert ejecucion.pasos[3].argumentos == '{"obraId": "b0000000-0000-4000-8000-000000000001", "texto": "lluvia"}'
    assert sorted(llamadas) == sorted(["avance_obra", "planillas_estado", "riesgo_retraso", "buscar_diario"])
    assert ejecucion.resumen == "Una obra atrasada por lluvia y con riesgo alto."
    assert [(h.obra_id, h.obra_nombre, h.tipo) for h in ejecucion.hallazgos] == [(O1, NOMBRE_O1, "ATRASO")]
    assert [(s.accion, s.estado, s.obra_nombre, s.hallazgo_indice) for s in sugerencias] == [
        ("SOLICITAR_INFORME_RESIDENTE", PENDIENTE, NOMBRE_O1, 0)
    ]
    # El modelo solo vio las herramientas del agente, y los datos delimitados y escapados.
    conversar = modelo.llamadas_de("conversar")[0]
    assert {h.nombre for h in conversar.herramientas} == {
        "avance_obra",
        "buscar_diario",
        "planillas_estado",
        "riesgo_retraso",
        "consultar_reporte",
    }
    reporte = next(h for h in conversar.herramientas if h.nombre == "consultar_reporte")
    assert reporte.esquema["properties"]["conjunto"]["enum"] == ["OBRAS", "PLANILLAS"]
    inicial = conversar.mensajes[1].contenido
    assert inicial.count("<datos_herramienta") == 3
    sistema = conversar.mensajes[0].contenido
    assert "Hoy es 2027-04-20" in sistema
    assert "2027-04" in sistema
    assert "SOLICITAR_INFORME_RESIDENTE" in sistema
    # En el bucle, el resultado vuelve como bloque de herramienta, delimitado.
    en_el_bucle = next(m for m in modelo.llamadas_de("conversar")[1].mensajes if m.rol is Rol.HERRAMIENTA)
    assert en_el_bucle.contenido.startswith('<datos_herramienta nombre="buscar_diario">')
    # Para el reporte (sin herramientas) los pedidos y resultados pasan a texto, con los datos igual de delimitados.
    reporte = modelo.llamadas_de("generar_json")[0].mensajes
    assert all(not m.llamadas and m.rol is not Rol.HERRAMIENTA for m in reporte)
    diario = next(m for m in reporte if m.contenido.startswith("Resultado de buscar_diario:"))
    assert TEXTO_DIARIO in diario.contenido
    assert '\n<datos_herramienta nombre="buscar_diario">' in diario.contenido
    assert any("Pedí la herramienta buscar_diario con" in m.contenido for m in reporte if m.rol is Rol.ASISTENTE)
    assert modelo.llamadas_de("generar_json")[0].opciones.contexto == ejecucion.id


async def test_camino_feliz_control_de_materiales(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    modelo = ModeloSimulado(
        json_=[reporte_materiales()],
        conversacion=[[llamada("consumos_partida", obraId=O1, periodo="2027-04")], "Listo."],
    )
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("CONTROL_MATERIALES")
        sugerencias = await e.sugerencias.listar(PENDIENTE, ["CONTROL_MATERIALES"], None, 50)
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert [p.herramienta for p in ejecucion.pasos] == [
        "stock_obra",
        "avance_obra",
        "consumos_atipicos",
        "consumos_partida",
    ]
    assert [h.tipo for h in ejecucion.hallazgos] == ["STOCK_BAJO", "CONSUMO_ATIPICO"]
    assert {s.accion for s in sugerencias} == {"VERIFICAR_INVENTARIO", "REVISAR_CONSUMO_ATIPICO"}


async def test_camino_feliz_control_de_cobros(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    modelo = ModeloSimulado(
        json_=[reporte_cobros()],
        conversacion=[
            [llamada("consultar_reporte", conjunto="COBROS", filtros=[{"campo": "estado", "operador": "IGUAL",
                                                                        "valor": "VENCIDO"}])],
            "Listo.",
        ],
    )  # fmt: skip
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("CONTROL_COBROS")
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert [p.herramienta for p in ejecucion.pasos] == ["cobros_por_vencer", "planillas_estado", "consultar_reporte"]
    assert [h.tipo for h in ejecucion.hallazgos] == ["COBRO_VENCIDO", "PLANILLA_SIN_RESPUESTA"]


async def test_reporte_invalido_dos_veces_es_fallida(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    malo = reporte_seguimiento(sugerencias=[{"obraId": O1, "accion": "APROBAR_PLANILLA", "descripcion": "x",
                                             "hallazgo": 0}])  # fmt: skip
    modelo = ModeloSimulado(json_=[malo, malo])
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("SEGUIMIENTO_OBRAS")
        sugerencias = await e.sugerencias.listar(None, ["SEGUIMIENTO_OBRAS"], None, 50)
    assert ejecucion.estado == FALLIDA
    assert ejecucion.error is not None
    assert ejecucion.error.codigo == "REPORTE_INVALIDO"
    assert len(ejecucion.pasos) == 3  # los pasos quedan guardados
    assert sugerencias == []
    reintento = modelo.llamadas_de("generar_json")[1].mensajes[-1].contenido
    assert "accion" in reintento


async def test_reporte_corregido_en_el_reintento(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    hallazgo = {**reporte_seguimiento()["hallazgos"][0], "evidencia": [{"herramienta": "avance_obra", "dato": "12,5"}]}
    malo = reporte_seguimiento(hallazgos=[{**hallazgo, "obraId": "b0000000-0000-4000-8000-0000000000ff"}])
    bueno = reporte_seguimiento(hallazgos=[hallazgo])
    modelo = ModeloSimulado(json_=[malo, bueno], conversacion=["Listo."])
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("SEGUIMIENTO_OBRAS")
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert "no salió" in modelo.llamadas_de("generar_json")[1].mensajes[-1].contenido


async def test_llm_caido_es_fallida(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    modelo = ModeloSimulado(conversacion=[LlmNoDisponible("caído")])
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("CONTROL_COBROS")
    assert ejecucion.estado == FALLIDA
    assert ejecucion.error is not None
    assert ejecucion.error.codigo == "LLM_NO_DISPONIBLE"
    assert ejecucion.error.mensaje == "El modelo de lenguaje no respondió."  # sin nombres técnicos (mensajes.md)


async def test_limite_de_tiempo_total(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    modelo = ModeloSimulado(conversacion=["Listo."], demora_s=5)
    async with entorno(ia_db, modelo, Limites(total_s=0.5)) as e:
        ejecucion = await e.correr("CONTROL_COBROS")
    assert ejecucion.estado == FALLIDA
    assert ejecucion.error is not None
    assert ejecucion.error.codigo == "EJECUCION_TIEMPO_AGOTADO"


async def test_paso_con_servidor_caido_y_error_de_herramienta_guardan_solo_el_codigo(
    ia_db: AsyncDatabase[dict[str, Any]],
) -> None:
    from agentes.apoyo_agentes import servidores_de_prueba

    servidores = servidores_de_prueba()
    servidores["finanzas"].caido = True
    modelo = ModeloSimulado(json_=[LlmNoDisponible("caído")], conversacion=["Listo."])
    async with entorno(ia_db, modelo, servidores=servidores) as e:
        ejecucion = await e.correr("CONTROL_COBROS")
    errores = [p.error for p in ejecucion.pasos if p.error]
    assert errores == ["SERVIDOR_NO_DISPONIBLE"]
    assert ejecucion.error is not None
    for texto in (ejecucion.error.mensaje, *errores):
        for tecnico in ("Llm", "Fallo", "Error", "MCP", "Exception", "finanzas.prueba"):
            assert tecnico not in texto, texto
