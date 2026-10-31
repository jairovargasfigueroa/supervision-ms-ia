"""Motivos en palabras simples de un consumo atípico (403, diseño «Motivos»).

El Isolation Forest no explica sus decisiones: los motivos salen de reglas sobre las variables con los percentiles del
entrenamiento (`percentiles.json`). Se devuelven de 1 a 3, del más fuerte al más débil.

Fuerza de cada regla (decisión del equipo, el diseño no la fija): en las de percentil, cuánto se pasa del umbral en
proporción a él (`1 + (valor − umbral) / |umbral|`); en las de sí/no, 1. A igual fuerza, el orden de la tabla del
diseño. Además, cada regla exige que el motivo tenga sentido: que la cantidad sea mayor que la habitual (veces > 1),
que haya al menos 2 salidas en el día y que haya una salida anterior.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ia.atipicos.dominio.variables import NOMBRES_VARIABLES, VariablesSalida

MAXIMO_MOTIVOS = 3
MOTIVO_GENERICO = "Combinación poco habitual de cantidad, horario y frecuencia"


@dataclass(frozen=True)
class Percentiles:
    """Percentiles 1 y 99 de cada variable en los datos de entrenamiento."""

    p1: Mapping[str, float]
    p99: Mapping[str, float]

    def como_dict(self) -> dict[str, dict[str, float]]:
        return {nombre: {"p1": self.p1[nombre], "p99": self.p99[nombre]} for nombre in NOMBRES_VARIABLES}

    @classmethod
    def desde_dict(cls, datos: Mapping[str, Any]) -> "Percentiles":
        """Lee `{"z_obra_material": {"p1": …, "p99": …}, …}`; falta una variable → `ValueError`."""
        faltan = [nombre for nombre in NOMBRES_VARIABLES if nombre not in datos]
        if faltan:
            raise ValueError(f"Faltan percentiles de: {', '.join(faltan)}.")
        return cls(
            p1={nombre: float(datos[nombre]["p1"]) for nombre in NOMBRES_VARIABLES},
            p99={nombre: float(datos[nombre]["p99"]) for nombre in NOMBRES_VARIABLES},
        )


def motivos_consumo(variables: VariablesSalida, percentiles: Percentiles) -> list[str]:
    candidatos: list[tuple[float, int, str]] = []

    def agregar(fuerza: float, texto: str) -> None:
        candidatos.append((fuerza, len(candidatos), texto))

    p99, p1 = percentiles.p99, percentiles.p1
    if (
        variables.veces_obra_material is not None
        and variables.veces_obra_material > 1
        and variables.z_obra_material >= p99["z_obra_material"]
    ):
        agregar(
            _fuerza_mayor(variables.z_obra_material, p99["z_obra_material"]),
            f"La cantidad es {_veces(variables.veces_obra_material)} veces la habitual de este material en la obra",
        )
    if variables.razon_material > 1 and variables.razon_material >= p99["razon_material"]:
        agregar(
            _fuerza_mayor(variables.razon_material, p99["razon_material"]),
            f"La cantidad es {_veces(variables.razon_material)} veces la habitual de este material en todas las obras",
        )
    if variables.razon_periodo > 1 and variables.razon_periodo >= p99["razon_periodo"]:
        agregar(
            _fuerza_mayor(variables.razon_periodo, p99["razon_periodo"]),
            f"El consumo del mes ya es {_veces(variables.razon_periodo)} veces el promedio mensual",
        )
    if variables.fuera_horario:
        agregar(1.0, f"Se registró fuera del horario habitual ({variables.hora_local})")
    if variables.domingo:
        agregar(1.0, "Se registró en domingo")
    if variables.supera_stock:
        agregar(1.0, "Salió más material del que había en stock")
    if variables.salidas_1h >= 2 and variables.salidas_1h >= p99["salidas_1h"]:
        agregar(
            _fuerza_mayor(variables.salidas_1h, p99["salidas_1h"]),
            f"Hubo {variables.salidas_1h} salidas de este material en la última hora",
        )
    if variables.salidas_dia >= 2 and variables.salidas_dia >= p99["salidas_dia"]:
        agregar(
            _fuerza_mayor(variables.salidas_dia, p99["salidas_dia"]),
            f"Hubo {variables.salidas_dia} salidas de este material el mismo día",
        )
    if variables.minutos_anterior is not None and variables.log_horas_anterior <= p1["log_horas_anterior"]:
        minutos = round(variables.minutos_anterior)
        agregar(
            _fuerza_menor(variables.log_horas_anterior, p1["log_horas_anterior"]),
            f"Salida muy seguida de la anterior ({minutos} {'minuto' if minutos == 1 else 'minutos'})",
        )

    if not candidatos:
        return [MOTIVO_GENERICO]
    candidatos.sort(key=lambda candidato: (-candidato[0], candidato[1]))
    return [texto for _, _, texto in candidatos[:MAXIMO_MOTIVOS]]


def _fuerza_mayor(valor: float, umbral: float) -> float:
    return 1 + (valor - umbral) / max(abs(umbral), 1e-9)


def _fuerza_menor(valor: float, umbral: float) -> float:
    return 1 + (umbral - valor) / max(abs(umbral), 1e-9)


def _veces(valor: float) -> str:
    """Un decimal con coma («4,2»), redondeado mitad hacia arriba."""
    return str(Decimal(repr(valor)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)).replace(".", ",")
