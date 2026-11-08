"""Puertos de los consumos atípicos (403)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
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

    async def listar(
        self,
        obra_ids: Sequence[str] | None,
        estado: str | None,
        desde: datetime | None,
        hasta: datetime | None,
        limite: int,
    ) -> list[ConsumoAtipico]:
        """De la más reciente a la más antigua (`registrado_en`); `obra_ids = None` = todas; `hasta` sin incluir."""
        ...

    async def pendientes_por_obra(self, obra_ids: Sequence[str]) -> dict[str, int]:
        """`PENDIENTE` de cada obra (las que no tienen, no vienen)."""
        ...

    async def buscar(
        self, obra_ids: Sequence[str] | None, estados: Sequence[str], desde: datetime, limite: int
    ) -> tuple[list[ConsumoAtipico], int]:
        """Solo lectura (501): `registrado_en >= desde`, del puntaje más alto al más bajo; y el total sin recortar."""
        ...

    async def revisar(
        self, consumo_id: str, estado: str, revisado_por: str, revisado_en: datetime, comentario: str | None
    ) -> ConsumoAtipico | None:
        """Actualización atómica solo si sigue `PENDIENTE`; `None` si no lo encontró pendiente."""
        ...


@dataclass(frozen=True)
class ObraConocida:
    obra_id: str
    residente_id: str | None
    nombre: str | None = None
    """Nombre de `rpt_obra_resumen` (lo usa la herramienta MCP `consumos_atipicos`, 501)."""


class ObrasReportes(Protocol):
    """Obras de `rpt_obra_resumen` (401): la visibilidad del residente sale de `residente_id`."""

    async def obras(self, obra_ids: Sequence[str]) -> dict[str, ObraConocida]:
        """Las que existen (ids que no son UUID se ignoran)."""
        ...

    async def obras_del_residente(self, residente_id: str) -> list[str]: ...


class NotificadorRiesgo(Protocol):
    """Recalcula el riesgo de retraso de una obra (402) después de detectar un consumo atípico en ella.

    Lo implementa `ia.atipicos.infraestructura.riesgo.RecalculoRiesgo402` con la predicción de la 402 (motivo
    `CONSUMO_ATIPICO`, clave `{obraId}:{eventoId}`). Sin notificador, el paso no existe.
    """

    async def consumo_atipico(self, obra_id: str, evento_id: str) -> None:
        """`evento_id`: el `id` del `MovimientoRegistrado` (idempotencia del recálculo)."""
        ...
