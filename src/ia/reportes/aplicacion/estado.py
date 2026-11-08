"""Estado de la base de reportes (`GET /reportes/estado`, solo `admin`): para comprobar la proyección y la carga."""

from dataclasses import dataclass
from datetime import datetime

from ia.reportes.aplicacion.puertos import BaseReportes, CorridaCarga


@dataclass(frozen=True)
class EstadoBaseReportes:
    tablas: dict[str, int]
    ultimos_eventos: list[tuple[str, datetime]]
    carga_inicial: CorridaCarga | None
    """La última corrida; `None` si nunca se corrió."""


class EstadoReportes:
    def __init__(self, base: BaseReportes) -> None:
        self._base = base

    async def consultar(self) -> EstadoBaseReportes:
        async with self._base.transaccion() as repo:
            return EstadoBaseReportes(
                tablas=await repo.contar_filas(),
                ultimos_eventos=await repo.ultimos_eventos(),
                carga_inicial=await repo.ultima_carga(),
            )
