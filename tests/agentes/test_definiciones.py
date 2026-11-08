"""Definiciones de los tres agentes (502): herramientas del contrato de la 501, pasos iniciales válidos, conjuntos del
catálogo de la 503, tipos y acciones iguales a los enums del gateway."""

import pytest

from ia.agentes.dominio.definiciones import (
    AGENTES,
    AGENTES_DEL_RESIDENTE,
    CONSULTAR_REPORTE,
    CONTROL_COBROS,
    texto_accion,
)
from ia.constructor_reportes.dominio.receta import CONJUNTOS
from ia.mcp.cliente.catalogo import CONTRATO

TIPOS_GATEWAY = {
    "ATRASO", "RIESGO_DE_ATRASO", "SIN_REGISTRO_DIARIO", "PLANILLA_PENDIENTE",
    "STOCK_BAJO", "STOCK_JUSTO", "CONSUMO_EXCEDIDO", "SALIDA_SIN_STOCK", "CONSUMO_ATIPICO",
    "COBRO_VENCIDO", "COBRO_POR_VENCER", "PLANILLA_SIN_RESPUESTA", "PLANILLA_OBSERVADA_SIN_CORREGIR",
}  # fmt: skip
ACCIONES_GATEWAY = {
    "REVISAR_PROGRAMA_OBRA", "PROGRAMAR_VISITA", "SOLICITAR_INFORME_RESIDENTE", "COMPLETAR_DIARIO_OBRA",
    "VERIFICAR_INVENTARIO", "REVISAR_CONSUMO_PARTIDA", "EVALUAR_REPOSICION", "REVISAR_SALIDAS_SIN_STOCK",
    "REVISAR_CONSUMO_ATIPICO", "CONTACTAR_CLIENTE_COBRO", "RECORDAR_REVISION_PLANILLA",
    "CORREGIR_PLANILLA_OBSERVADA", "REVISAR_SALDO_CONTRATO",
}  # fmt: skip


def test_tres_agentes_en_orden() -> None:
    assert list(AGENTES) == ["SEGUIMIENTO_OBRAS", "CONTROL_MATERIALES", "CONTROL_COBROS"]
    assert CONTROL_COBROS not in AGENTES_DEL_RESIDENTE


def test_herramientas_por_agente_segun_el_diseno() -> None:
    assert {a: set(d.herramientas) for a, d in AGENTES.items()} == {
        "SEGUIMIENTO_OBRAS": {"avance_obra", "buscar_diario", "planillas_estado", "riesgo_retraso", CONSULTAR_REPORTE},
        "CONTROL_MATERIALES": {"stock_obra", "consumos_partida", "avance_obra", "consumos_atipicos", CONSULTAR_REPORTE},
        "CONTROL_COBROS": {"cobros_por_vencer", "planillas_estado", CONSULTAR_REPORTE},
    }
    assert {a: d.conjuntos for a, d in AGENTES.items()} == {
        "SEGUIMIENTO_OBRAS": ("OBRAS", "PLANILLAS"),
        "CONTROL_MATERIALES": ("CONSUMO_MATERIAL", "MOVIMIENTOS"),
        "CONTROL_COBROS": ("COBROS", "PLANILLAS"),
    }
    for definicion in AGENTES.values():
        assert set(definicion.herramientas) <= set(CONTRATO)
        assert set(definicion.conjuntos) <= set(CONJUNTOS)


@pytest.mark.parametrize("agente", list(AGENTES))
def test_pasos_iniciales_son_herramientas_del_agente_con_argumentos_validos(agente: str) -> None:
    definicion = AGENTES[agente]
    for paso in definicion.pasos_iniciales:
        assert paso.herramienta in definicion.herramientas
        CONTRATO[paso.herramienta].entrada.model_validate(dict(paso.argumentos))


def test_tipos_y_acciones_iguales_a_los_enums_del_gateway() -> None:
    tipos = [t for d in AGENTES.values() for t in d.tipos]
    acciones = [a for d in AGENTES.values() for a in d.acciones]
    assert len(tipos) == len(set(tipos)) == 13
    assert set(tipos) == TIPOS_GATEWAY
    assert len(acciones) == len(set(acciones)) == 13
    assert set(acciones) == ACCIONES_GATEWAY
    assert texto_accion("CONTROL_MATERIALES", "VERIFICAR_INVENTARIO") == (
        "Hacer un conteo físico del material en el depósito de la obra"
    )
    assert texto_accion("CONTROL_COBROS", "VERIFICAR_INVENTARIO") == ""
    assert "APROBAR_PLANILLA" not in acciones
