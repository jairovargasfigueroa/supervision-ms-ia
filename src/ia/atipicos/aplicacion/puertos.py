"""Puertos de los consumos atípicos (403)."""

from dataclasses import dataclass
from typing import Protocol

from ia.atipicos.dominio.consumo import ConsumoAtipico
from ia.atipicos.dominio.variables import Historial, SalidaMaterial, VariablesSalida


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


class FuenteHistorial(Protocol):
    async def historial_de(self, salida: SalidaMaterial) -> Historial:
        """Salidas de `rpt_movimiento` con `registrado_en` anterior a la evaluada y otro `movimiento_id`."""
        ...


class EventosAplicados(Protocol):
    async def ya_aplicado(self, evento_id: str) -> bool:
        """`True` si el `id` del evento está en `evento_aplicado` (inbox de la 401)."""
        ...


class RepositorioAtipicos(Protocol):
    """`ia_db.consumo_atipico`."""

    async def guardar_si_no_existe(self, consumo: ConsumoAtipico) -> bool:
        """`$setOnInsert` por `_id` (idempotente). `True` si lo insertó."""
        ...

    async def obtener(self, consumo_id: str) -> ConsumoAtipico | None: ...
