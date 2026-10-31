"""Puertos de los consumos atípicos (403)."""

from dataclasses import dataclass
from typing import Protocol

from ia.atipicos.dominio.variables import VariablesSalida


@dataclass(frozen=True)
class Evaluacion:
    puntaje: float
    """`−score_samples(x)`: de 0 a 1, más alto = más raro."""
    atipica: bool
    """`predict(x) = −1`."""
    motivos: list[str]
    """De 1 a 3 (solo tienen sentido si es atípica)."""
    version: str


class ModeloAtipicos(Protocol):
    """Isolation Forest de una versión de `modelos/consumos_atipicos/`, con sus percentiles."""

    @property
    def version(self) -> str: ...

    def evaluar(self, variables: VariablesSalida) -> Evaluacion: ...
