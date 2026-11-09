"""Ejecutor de los agentes (502) con `ModeloSimulado`, servidores MCP de prueba y MongoDB real."""

from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

import agentes.apoyo_agentes as apoyo
from agentes.apoyo_agentes import (
    CEMENTO,
    NOMBRE_O1,
    NOMBRE_O2,
    O1,
    O2,
    TEXTO_DIARIO,
    entorno,
    evidencia,
    reporte_cobros,
    reporte_materiales,
    reporte_seguimiento,
)
from dobles.modelo_simulado import ModeloSimulado, llamada
from ia.agentes.aplicacion.ejecutor import Limites
from ia.agentes.aplicacion.puertos import ConectorMcp, SesionMcp
from ia.agentes.dominio.ejecucion import FALLIDA, PENDIENTE, TERMINADA
from ia.lenguaje.aplicacion.puerto import LlmNoDisponible
from ia.lenguaje.dominio.mensajes import Rol
from ia.mcp.cliente.cliente import FalloMcp, HerramientaPublicada, ResultadoHerramienta

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
        "consumos_atipicos",
        "avance_obra",  # paso compuesto consumo_vs_previsto (505)
        "consumos_partida",
        "consumos_partida",  # el que pidió el modelo
    ]
    assert [p.argumentos for p in ejecucion.pasos[2:4]] == ['{"periodo": "2027-04"}'] * 2  # $periodo_actual
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


async def test_reporte_invalido_tres_veces_es_fallida(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    malo = reporte_seguimiento(sugerencias=[{"obraId": O1, "accion": "APROBAR_PLANILLA", "descripcion": "x",
                                             "hallazgo": 0}])  # fmt: skip
    modelo = ModeloSimulado(json_=[malo] * 3)
    async with entorno(ia_db, modelo) as e:
        ejecucion = await e.correr("SEGUIMIENTO_OBRAS")
        sugerencias = await e.sugerencias.listar(None, ["SEGUIMIENTO_OBRAS"], None, 50)
    assert ejecucion.estado == FALLIDA
    assert ejecucion.error is not None
    assert ejecucion.error.codigo == "REPORTE_INVALIDO"
    assert len(ejecucion.pasos) == 3  # los pasos quedan guardados
    assert sugerencias == []
    assert len(modelo.llamadas_de("generar_json")) == 3  # 505: hasta 3 intentos
    reintento = modelo.llamadas_de("generar_json")[2].mensajes[-1].contenido
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


async def test_salida_ilegible_del_reporte_tiene_un_reintento(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    from ia.lenguaje.aplicacion.puerto import SalidaNoValida

    hallazgo = {**reporte_seguimiento()["hallazgos"][0], "evidencia": [{"herramienta": "avance_obra", "dato": "x"}]}
    bueno = reporte_seguimiento(hallazgos=[hallazgo])
    modelo = ModeloSimulado(json_=[SalidaNoValida("JSON cortado"), bueno], conversacion=["Listo."])
    async with entorno(ia_db, modelo) as e:
        corregida = await e.correr("SEGUIMIENTO_OBRAS")
    assert corregida.estado == TERMINADA, corregida.error
    assert "JSON" in modelo.llamadas_de("generar_json")[1].mensajes[-1].contenido

    tres_veces = ModeloSimulado(json_=[SalidaNoValida("x"), SalidaNoValida("y"), SalidaNoValida("z")],
                                conversacion=["Listo."])  # fmt: skip
    async with entorno(ia_db, tres_veces) as e:
        fallida = await e.correr("CONTROL_COBROS")
    assert fallida.estado == FALLIDA
    assert fallida.error is not None
    assert fallida.error.codigo == "REPORTE_INVALIDO"


async def test_cada_llamada_al_modelo_se_recorta_al_tiempo_que_queda(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    """Criterio 5: al llegar al límite de tiempo del bucle se cierra el reporte con lo que hay, sin pasar el total."""
    modelo = ModeloSimulado(
        json_=[reporte_cobros()],
        conversacion=[[llamada("planillas_estado")], [llamada("planillas_estado")], "Listo."],
        demora_s=1.2,
    )
    async with entorno(ia_db, modelo, Limites(total_s=3.0, bucle_s=1.5)) as e:
        ejecucion = await e.correr("CONTROL_COBROS")
    assert ejecucion.estado == TERMINADA, ejecucion.error
    tiempos = [x.opciones.tiempo_maximo_s for x in modelo.llamadas]
    assert tiempos[1] < 0.5  # la segunda vuelta solo tuvo lo que quedaba del bucle
    assert tiempos[2] <= 1.8  # el reporte, lo que quedaba del total


# --- 505: paso compuesto consumo_vs_previsto y reintento MCP -------------------------------------------------------

ARENA = "e0000000-0000-4000-8000-000000000002"
AVANCE_SIN_PARTIDAS = apoyo.AVANCE


def _avance_lote(argumentos: dict[str, Any]) -> dict[str, Any]:
    if argumentos.get("obraId") or not argumentos.get("periodo"):
        return AVANCE_SIN_PARTIDAS
    partidas = [
        {"partidaId": "p1", "codigo": "03.01", "nombre": "Losa", "materialPrevisto": [
            {"materialId": CEMENTO, "cantidadPrevista": 100},
            {"materialId": ARENA, "cantidadPrevista": 10},
        ]},
    ]  # fmt: skip
    return {
        "generadoEn": "2027-04-20T10:00:00Z",
        "obras": [{**AVANCE_SIN_PARTIDAS["obras"][0], "partidas": partidas}, {**AVANCE_SIN_PARTIDAS["obras"][1],
                                                                              "partidas": []}],
        "hayPlanillaEnPeriodo": None,
        "truncado": False,
    }  # fmt: skip


def _consumos_lote(argumentos: dict[str, Any]) -> dict[str, Any]:
    def fila(obra: str, nombre_obra: str, material: str, nombre: str, usado: float, sin_stock: int) -> dict[str, Any]:
        return {"obraId": obra, "obraNombre": nombre_obra, "partidaId": "p1", "materialId": material, "codigo": "X",
                "nombre": nombre, "unidad": "u", "usado": usado, "salidas": 3,
                "salidasSuperanStock": sin_stock}  # fmt: skip

    return {
        "obraId": argumentos.get("obraId"),
        "periodo": argumentos["periodo"],
        "cerrado": None,
        "filas": [
            fila(O1, NOMBRE_O1, CEMENTO, "Cemento IP-30", 150.0, 0),  # 150 % de lo previsto
            fila(O1, NOMBRE_O1, ARENA, "Arena fina", 11.0, 0),  # 110 %: dentro de lo previsto
            fila(O2, NOMBRE_O2, CEMENTO, "Cemento IP-30", 40.0, 2),  # sin previsto; 2 salidas sin stock
        ],
        "truncado": False,
    }


async def test_consumo_vs_previsto_entrega_solo_el_cruce(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    from agentes.apoyo_agentes import servidores_de_prueba

    servidores = servidores_de_prueba()
    servidores["obras"].herramienta("avance_obra").respuesta = _avance_lote
    servidores["materiales"].herramienta("consumos_partida").respuesta = _consumos_lote
    reporte = {
        "resumen": "Cemento usado al 150 % de lo previsto en Las Palmas.",
        "hallazgos": [
            {"obraId": O1, "tipo": "CONSUMO_EXCEDIDO", "severidad": "ALTA", "situacion": "Cemento: 150 de 100",
             "causaProbable": "No determinada", "evidencia": evidencia("consumos_partida", "porcentaje 150")},
            {"obraId": O2, "tipo": "SALIDA_SIN_STOCK", "severidad": "MEDIA", "situacion": "2 salidas sin stock",
             "causaProbable": "No determinada", "evidencia": evidencia("consumos_partida", "salidasSuperanStock 2")},
        ],
        "sugerencias": [
            {"obraId": O1, "accion": "REVISAR_CONSUMO_PARTIDA", "descripcion": "Revisar la losa", "hallazgo": 0},
        ],
    }  # fmt: skip
    modelo = ModeloSimulado(json_=[reporte], conversacion=["Listo."])
    async with entorno(ia_db, modelo, servidores=servidores) as e:
        ejecucion = await e.correr("CONTROL_MATERIALES")
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert [h.tipo for h in ejecucion.hallazgos] == ["CONSUMO_EXCEDIDO", "SALIDA_SIN_STOCK"]
    assert servidores["materiales"].llamadas[-1] == ("consumos_partida", {"periodo": "2027-04"})
    inicial = modelo.llamadas_de("conversar")[0].mensajes[1].contenido
    assert inicial.count("<datos_herramienta") == 3  # stock, atípicos y el cruce (no los datos crudos)
    cruce = inicial.split("(evidencia: consumos_partida):\n")[1]
    assert cruce.startswith('<datos_herramienta nombre="consumos_partida">')
    assert '"porcentaje": 150.0' in cruce and '"previsto": 100.0' in cruce and '"usado": 150.0' in cruce
    assert '"salidasSuperanStock": 2' in cruce and NOMBRE_O2 in cruce
    assert ARENA not in cruce and "Arena fina" not in cruce  # dentro de lo previsto: no llega al modelo
    assert "Si algo no se pudo revisar" in modelo.llamadas_de("conversar")[0].mensajes[0].contenido


class _FallaAlLlamar:
    """Conector que hace fallar (`FalloMcp`) las primeras `veces` llamadas a una herramienta."""

    def __init__(self, herramienta: str, veces: int) -> None:
        self.herramienta = herramienta
        self.veces = veces

    def __call__(self, conector: ConectorMcp) -> ConectorMcp:
        padre = self

        class _Conector:
            def sesion(self, ejecucion_id: str) -> SesionMcp:
                real = conector.sesion(ejecucion_id)

                class _Sesion:
                    async def listar(self, servidor: str) -> list[HerramientaPublicada]:
                        return await real.listar(servidor)

                    async def llamar(
                        self, servidor: str, herramienta: str, argumentos: dict[str, Any]
                    ) -> ResultadoHerramienta:
                        if herramienta == padre.herramienta and padre.veces > 0:
                            padre.veces -= 1
                            raise FalloMcp("conexión cortada")
                        return await real.llamar(servidor, herramienta, argumentos)

                return _Sesion()

        return _Conector()


async def test_llamada_mcp_que_falla_una_vez_se_reintenta(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    modelo = ModeloSimulado(json_=[reporte_cobros()], conversacion=["Listo."])
    falla = _FallaAlLlamar("planillas_estado", veces=1)
    async with entorno(ia_db, modelo, Limites(espera_mcp_s=0.01), envolver=falla) as e:
        ejecucion = await e.correr("CONTROL_COBROS")
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert falla.veces == 0
    assert [(p.herramienta, p.error) for p in ejecucion.pasos] == [
        ("cobros_por_vencer", None),
        ("planillas_estado", None),
    ]
    assert ejecucion.resumen == "Un cobro vencido y una planilla sin respuesta."  # sin «Sin respuesta de»


async def test_llamada_mcp_que_falla_dos_veces_queda_sin_respuesta(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    sin_planillas = reporte_cobros()
    sin_planillas["hallazgos"] = sin_planillas["hallazgos"][:1]
    modelo = ModeloSimulado(json_=[sin_planillas], conversacion=["Listo."])
    falla = _FallaAlLlamar("planillas_estado", veces=2)
    async with entorno(ia_db, modelo, Limites(espera_mcp_s=0.01), envolver=falla) as e:
        ejecucion = await e.correr("CONTROL_COBROS")
    assert ejecucion.estado == TERMINADA, ejecucion.error
    assert [(p.herramienta, p.error) for p in ejecucion.pasos] == [
        ("cobros_por_vencer", None),
        ("planillas_estado", "SERVIDOR_NO_DISPONIBLE"),
    ]
    assert ejecucion.resumen is not None and ejecucion.resumen.endswith("(Sin respuesta de: obras.)")
