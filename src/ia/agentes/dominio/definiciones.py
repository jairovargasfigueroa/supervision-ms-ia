"""Los tres agentes (502): los define el equipo en código, no por configuración.

Cada agente tiene su nombre visible, su misión (la corta, que ve la persona en «Agentes», y la del mensaje de
sistema), sus herramientas MCP (501) con los conjuntos de `consultar_reporte` que puede pedir, sus pasos iniciales,
sus tipos de hallazgo y sus acciones permitidas con el texto que ve la persona.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

SEGUIMIENTO_OBRAS: Final = "SEGUIMIENTO_OBRAS"
CONTROL_MATERIALES: Final = "CONTROL_MATERIALES"
CONTROL_COBROS: Final = "CONTROL_COBROS"

CONSULTAR_REPORTE: Final = "consultar_reporte"


@dataclass(frozen=True)
class PasoInicial:
    herramienta: str
    argumentos: Mapping[str, Any]


@dataclass(frozen=True)
class DefinicionAgente:
    agente: str
    nombre: str
    mision: str
    """La que ve la persona (tarjeta del agente, `GET /agentes`)."""
    instrucciones: str
    """La misión del mensaje de sistema."""
    herramientas: tuple[str, ...]
    conjuntos: tuple[str, ...]
    """Conjuntos de `consultar_reporte` que puede pedir (mínimo privilegio)."""
    pasos_iniciales: tuple[PasoInicial, ...]
    tipos: tuple[str, ...]
    acciones: Mapping[str, str]
    """Acción → texto que ve la persona."""


def _acciones(*pares: tuple[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(pares))


SEGUIMIENTO = DefinicionAgente(
    agente=SEGUIMIENTO_OBRAS,
    nombre="Seguimiento de obras",
    mision=(
        "Detectar obras atrasadas, que van camino a atrasarse o con riesgo alto según el modelo, buscar en el diario "
        "la causa probable y revisar si la planilla del mes está pendiente"
    ),
    instrucciones=(
        "Revisa las obras en ejecución. Identifica las atrasadas (plazo − avance mayor a 10 puntos), las que se "
        "acercan (entre 5 y 10 puntos) y las que el modelo de riesgo marca con nivel ALTO o MEDIO. Para cada una, "
        "busca en el diario de los últimos 14 días la causa probable (clima, falta de material, falta de personal, "
        "otra). Revisa si la planilla del mes anterior está pendiente. Reporta solo lo que puedas respaldar con datos "
        "de las herramientas."
    ),
    herramientas=("avance_obra", "buscar_diario", "planillas_estado", "riesgo_retraso", CONSULTAR_REPORTE),
    conjuntos=("OBRAS", "PLANILLAS"),
    pasos_iniciales=(
        PasoInicial("avance_obra", MappingProxyType({})),
        PasoInicial(
            "planillas_estado", MappingProxyType({"estados": ["BORRADOR", "PRESENTADA", "OBSERVADA", "APROBANDO"]})
        ),
        PasoInicial("riesgo_retraso", MappingProxyType({"niveles": ["ALTO", "MEDIO"]})),
    ),
    tipos=("ATRASO", "RIESGO_DE_ATRASO", "SIN_REGISTRO_DIARIO", "PLANILLA_PENDIENTE"),
    acciones=_acciones(
        ("REVISAR_PROGRAMA_OBRA", "Revisar el programa de trabajo de la obra con el residente"),
        ("PROGRAMAR_VISITA", "Programar una visita de supervisión a la obra"),
        ("SOLICITAR_INFORME_RESIDENTE", "Pedir al residente un informe del atraso y su plan de recuperación"),
        ("COMPLETAR_DIARIO_OBRA", "Recordar al maestro de obra que registre el diario"),
    ),
)

MATERIALES = DefinicionAgente(
    agente=CONTROL_MATERIALES,
    nombre="Control de materiales",
    mision=(
        "Detectar stock bajo o justo, salidas que superaron el stock, consumos por encima de lo previsto en el periodo "
        "y consumos atípicos sin revisar"
    ),
    instrucciones=(
        "Revisa el stock de las obras en ejecución. Identifica materiales en estado BAJO o JUSTO, salidas que "
        "superaron el stock, consumos atípicos pendientes de revisión y, para el periodo actual, partidas donde el "
        "material usado supera en más de 20 % al previsto (usado > 1,20 × previsto). Reporta solo lo que puedas "
        "respaldar con datos."
    ),
    herramientas=("stock_obra", "consumos_partida", "avance_obra", "consumos_atipicos", CONSULTAR_REPORTE),
    conjuntos=("CONSUMO_MATERIAL", "MOVIMIENTOS"),
    pasos_iniciales=(
        PasoInicial("stock_obra", MappingProxyType({"estados": ["BAJO", "JUSTO"]})),
        PasoInicial("avance_obra", MappingProxyType({})),
        PasoInicial("consumos_atipicos", MappingProxyType({})),
    ),
    tipos=("STOCK_BAJO", "STOCK_JUSTO", "CONSUMO_EXCEDIDO", "SALIDA_SIN_STOCK", "CONSUMO_ATIPICO"),
    acciones=_acciones(
        ("VERIFICAR_INVENTARIO", "Hacer un conteo físico del material en el depósito de la obra"),
        ("REVISAR_CONSUMO_PARTIDA", "Revisar con el residente el consumo de material de la partida"),
        (
            "EVALUAR_REPOSICION",
            "Evaluar la reposición del material (la orden de compra la gestiona la automatización de la capa 2)",
        ),
        ("REVISAR_SALIDAS_SIN_STOCK", "Revisar las salidas registradas sin stock suficiente"),
        (
            "REVISAR_CONSUMO_ATIPICO",
            "Revisar el consumo atípico en la pestaña «Material» de la obra y marcarlo como normal o confirmado",
        ),
    ),
)

COBROS = DefinicionAgente(
    agente=CONTROL_COBROS,
    nombre="Control de cobros",
    mision=(
        "Detectar cobros vencidos o por vencer y planillas presentadas sin respuesta del cliente u observadas sin "
        "corregir"
    ),
    instrucciones=(
        "Revisa los cobros vencidos y los que vencen en los próximos 15 días, y las planillas presentadas hace más de "
        "7 días sin respuesta del cliente o las observadas sin corregir. Reporta solo lo que puedas respaldar con "
        "datos."
    ),
    herramientas=("cobros_por_vencer", "planillas_estado", CONSULTAR_REPORTE),
    conjuntos=("COBROS", "PLANILLAS"),
    pasos_iniciales=(
        PasoInicial("cobros_por_vencer", MappingProxyType({"dias": 15, "incluirVencidos": True})),
        PasoInicial("planillas_estado", MappingProxyType({"estados": ["PRESENTADA", "OBSERVADA"]})),
    ),
    tipos=("COBRO_VENCIDO", "COBRO_POR_VENCER", "PLANILLA_SIN_RESPUESTA", "PLANILLA_OBSERVADA_SIN_CORREGIR"),
    acciones=_acciones(
        ("CONTACTAR_CLIENTE_COBRO", "Contactar al cliente por el cobro vencido o por vencer"),
        ("RECORDAR_REVISION_PLANILLA", "Recordar al cliente que revise la planilla presentada"),
        ("CORREGIR_PLANILLA_OBSERVADA", "Pedir al residente que corrija y vuelva a presentar la planilla"),
        ("REVISAR_SALDO_CONTRATO", "Revisar el saldo disponible del contrato"),
    ),
)

AGENTES: Final[Mapping[str, DefinicionAgente]] = MappingProxyType(
    {a.agente: a for a in (SEGUIMIENTO, MATERIALES, COBROS)}
)
"""En el orden de la ejecución diaria."""

AGENTES_DEL_RESIDENTE: Final = (SEGUIMIENTO_OBRAS, CONTROL_MATERIALES)
"""El residente nunca ve Control de cobros."""


def texto_accion(agente: str, accion: str) -> str:
    definicion = AGENTES.get(agente)
    return "" if definicion is None else definicion.acciones.get(accion, "")
