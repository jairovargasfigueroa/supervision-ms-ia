"""Reportes comunes (atajos): una receta fija armada en función de «hoy» (La Paz).

Se validan con el mismo `validar_receta` (modo REST) y **no** llaman al modelo para entender el pedido: funcionan
aunque el modelo de lenguaje no esté disponible.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from ia.constructor_reportes.dominio.validacion import periodo_de, sumar_meses


@dataclass(frozen=True)
class Atajo:
    atajo: str
    titulo: str
    descripcion: str
    receta: Callable[[date], dict[str, Any]]


def _f(campo: str, operador: str, valor: Any) -> dict[str, Any]:
    return {"campo": campo, "operador": operador, "valor": valor}


ATAJOS: tuple[Atajo, ...] = (
    Atajo(
        "AVANCE_OBRAS",
        "Avance y plazo de las obras en ejecución",
        "Avance, plazo transcurrido y diferencia de cada obra en ejecución, de la más atrasada a la menos.",
        lambda hoy: {"conjunto": "OBRAS"},
    ),
    Atajo(
        "OBRAS_EN_RIESGO",
        "Obras con riesgo de retraso alto o medio",
        "Obras en ejecución que el modelo de riesgo (402) marca con nivel alto o medio.",
        lambda hoy: {
            "conjunto": "OBRAS",
            "filtros": [_f("nivelRiesgo", "EN", ["ALTO", "MEDIO"])],
            "columnas": ["obra", "zona", "avance_pct", "plazo_pct", "riesgo_pct", "nivel_riesgo"],
            "orden": [{"por": "riesgo_pct", "direccion": "DESC"}],
        },
    ),
    Atajo(
        "CONSUMO_MATERIAL_MES",
        "Material usado este mes",
        "Cantidad, monto referencial y salidas de cada material por obra en el mes actual.",
        lambda hoy: {"conjunto": "CONSUMO_MATERIAL"},
    ),
    Atajo(
        "SALIDAS_SIN_STOCK",
        "Salidas que superaron el stock (últimos 3 meses)",
        "Salidas registradas sin stock suficiente, por obra, material y periodo.",
        lambda hoy: {
            "conjunto": "MOVIMIENTOS",
            # «Últimos 3 meses»: el periodo por defecto de MOVIMIENTOS.
            "filtros": [_f("tipo", "IGUAL", "SALIDA"), _f("soloSuperaStock", "IGUAL", True)],
            "agruparPor": ["obra", "material", "periodo"],
            "metricas": [
                {"funcion": "CONTEO"},
                {"funcion": "SUMA", "campo": "cantidad"},
                {"funcion": "MAXIMO", "campo": "fecha"},
            ],
            "orden": [{"por": "conteo", "direccion": "DESC"}],
        },
    ),
    Atajo(
        "COBROS_VENCIDOS",
        "Cobros vencidos",
        "Cobros emitidos que ya vencieron, del más antiguo al más reciente.",
        lambda hoy: {
            "conjunto": "COBROS",
            "filtros": [_f("estado", "IGUAL", "VENCIDO")],
            "columnas": ["numero", "obra", "cliente", "monto", "vence_en"],
            "orden": [{"por": "vence_en", "direccion": "ASC"}],
        },
    ),
    Atajo(
        "COBROS_POR_VENCER",
        "Cobros que vencen en los próximos 30 días",
        "Cobros pendientes que vencen entre hoy y dentro de 30 días.",
        lambda hoy: {
            "conjunto": "COBROS",
            "filtros": [_f("estado", "IGUAL", "PENDIENTE"), _f("venceEnDias", "IGUAL", 30)],
            "columnas": ["numero", "obra", "monto", "vence_en", "dias_para_vencer"],
            "orden": [{"por": "vence_en", "direccion": "ASC"}],
        },
    ),
    Atajo(
        "COBRADO_POR_OBRA",
        "Total cobrado por obra este año",
        "Cobros emitidos desde enero, por obra: cantidad, total y monto vencido.",
        lambda hoy: {
            "conjunto": "COBROS",
            "filtros": [_f("periodo", "DESDE", f"{hoy.year:04d}-01")],
            "agruparPor": ["obra"],
            "metricas": [
                {"funcion": "CONTEO"},
                {"funcion": "SUMA", "campo": "monto"},
                {"funcion": "SUMA", "campo": "monto_vencido"},
            ],
            "orden": [{"por": "suma_monto", "direccion": "DESC"}],
        },
    ),
    Atajo(
        "PLANILLAS_OBSERVADAS",
        "Planillas observadas",
        "Planillas con observación, con las veces que se observaron y el último motivo.",
        lambda hoy: {
            "conjunto": "PLANILLAS",
            "filtros": [_f("estado", "IGUAL", "OBSERVADA")],
            "columnas": ["obra", "cliente", "periodo", "veces_observada", "ultimo_origen", "ultimo_motivo"],
            "orden": [{"por": "veces_observada", "direccion": "DESC"}],
        },
    ),
    Atajo(
        "PLANILLAS_APROBADAS",
        "Planillas aprobadas (últimos 3 meses)",
        "Planillas aprobadas de los últimos 3 periodos, con su neto y avance del periodo.",
        lambda hoy: {
            "conjunto": "PLANILLAS",
            "filtros": [_f("estado", "IGUAL", "APROBADA"), _f("periodo", "DESDE", periodo_de(sumar_meses(hoy, -2)))],
            "columnas": ["obra", "periodo", "neto", "avance_periodo_pct", "aprobada_en"],
            "orden": [{"por": "periodo", "direccion": "DESC"}],
        },
    ),
    Atajo(
        "CONSUMOS_ATIPICOS_PENDIENTES",
        "Consumos atípicos sin revisar",
        "Salidas de material marcadas como atípicas (403) que todavía nadie revisó.",
        lambda hoy: {"conjunto": "CONSUMOS_ATIPICOS", "filtros": [_f("estadoRevision", "IGUAL", "PENDIENTE")]},
    ),
)

POR_CODIGO: dict[str, Atajo] = {a.atajo: a for a in ATAJOS}
