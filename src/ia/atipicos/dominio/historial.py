"""Historial en memoria para recorrer muchas salidas en orden de fecha (generador sintético y reentrenamiento).

Mantiene por obra y material las ventanas que usa `calcular_variables_salida` (últimas 50 salidas, consumo por periodo)
y por material las últimas 200 cantidades, sin volver a filtrar toda la tabla en cada salida (diseño, «Datos
sintéticos», paso 5). El worker arma el mismo `Historial` con consultas a `rpt_movimiento`.
"""

from collections import defaultdict, deque
from collections.abc import Iterable, Iterator

from ia.atipicos.dominio.variables import (
    VENTANA_MATERIAL,
    VENTANA_OBRA_MATERIAL,
    Historial,
    SalidaMaterial,
    SalidaPrevia,
    VariablesSalida,
    calcular_variables_salida,
)


class HistorialEnMemoria:
    def __init__(self) -> None:
        self._obra_material: defaultdict[tuple[str, str], deque[SalidaPrevia]] = defaultdict(
            lambda: deque(maxlen=VENTANA_OBRA_MATERIAL)
        )
        self._material: defaultdict[str, deque[float]] = defaultdict(lambda: deque(maxlen=VENTANA_MATERIAL))
        self._periodos: defaultdict[tuple[str, str], dict[str, float]] = defaultdict(dict)

    def historial_de(self, salida: SalidaMaterial) -> Historial:
        clave = (salida.obra_id, salida.material_id)
        return Historial(
            obra_material=tuple(self._obra_material[clave]),
            material=tuple(self._material[salida.material_id]),
            consumo_por_periodo=dict(self._periodos[clave]),
        )

    def agregar(self, salida: SalidaMaterial) -> None:
        clave = (salida.obra_id, salida.material_id)
        self._obra_material[clave].append(SalidaPrevia(salida.cantidad, salida.registrado_en))
        self._material[salida.material_id].append(salida.cantidad)
        periodos = self._periodos[clave]
        periodos[salida.periodo] = periodos.get(salida.periodo, 0.0) + salida.cantidad


def recorrer_en_orden(salidas: Iterable[SalidaMaterial]) -> Iterator[tuple[SalidaMaterial, VariablesSalida]]:
    """Variables de cada salida con su historial anterior, en orden de `registrado_en` (y de id, si empatan).

    Como en el worker (`registrado_en` estrictamente anterior), las salidas del mismo instante no se ven entre sí.
    """
    historial = HistorialEnMemoria()
    mismo_instante: list[SalidaMaterial] = []
    for salida in sorted(salidas, key=lambda s: (s.registrado_en, s.movimiento_id)):
        if mismo_instante and mismo_instante[0].registrado_en != salida.registrado_en:
            for anterior in mismo_instante:
                historial.agregar(anterior)
            mismo_instante.clear()
        yield salida, calcular_variables_salida(salida, historial.historial_de(salida))
        mismo_instante.append(salida)
