"""Modelos de prueba de la 403: uno fijo (decide la prueba) y uno real chico (Isolation Forest con datos sintéticos)."""

from dataclasses import dataclass, field

import numpy as np

from ia.atipicos.aplicacion.puertos import Evaluacion
from ia.atipicos.dominio.variables import NOMBRES_VARIABLES, VariablesSalida
from ia.atipicos.infraestructura.modelo import ModeloAtipicosSklearn
from ml.atipicos.entrenar import calcular_percentiles, nuevo_modelo
from ml.atipicos.generar import calcular_filas, generar_salidas, leer_parametros


@dataclass
class ModeloFijo:
    """Marca como atípica según `atipica`; guarda las variables que recibió."""

    atipica: bool = True
    puntaje: float = 0.71
    motivos: list[str] = field(default_factory=lambda: ["Se registró fuera del horario habitual (22:40)"])
    version_modelo: str = "ca-0.0.1"
    recibidas: list[VariablesSalida] = field(default_factory=list)

    @property
    def version(self) -> str:
        return self.version_modelo

    def evaluar(self, variables: VariablesSalida) -> Evaluacion:
        self.recibidas.append(variables)
        return Evaluacion(self.puntaje, self.atipica, list(self.motivos) if self.atipica else [], self.version_modelo)


def modelo_sintetico_chico(obras: int = 8) -> ModeloAtipicosSklearn:
    """Isolation Forest con los parámetros del diseño, entrenado con pocas obras sintéticas (menos de 1 s)."""
    parametros = leer_parametros()
    parametros["obras"] = obras
    filas = calcular_filas(generar_salidas(parametros, 2026))
    x = np.array([[fila[nombre] for nombre in NOMBRES_VARIABLES] for fila in filas], dtype=float)
    return ModeloAtipicosSklearn(nuevo_modelo().fit(x), calcular_percentiles(x), "ca-0.9.0")
