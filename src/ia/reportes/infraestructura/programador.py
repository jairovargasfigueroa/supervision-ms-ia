"""Tareas programadas con apscheduler (`AsyncIOScheduler`) en la zona del negocio."""

import logging
from collections.abc import Awaitable, Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ia.reportes.aplicacion.tareas_programadas import TareasProgramadas

registro = logging.getLogger("ia.reportes.programador")

# (id, hora, minuto): 00:20 cobros vencidos, 00:30 plazo transcurrido (diseño de la 401).
HORARIO = (("vencer_cobros", 0, 20), ("recalcular_plazos", 0, 30))


def _seguro(nombre: str, tarea: Callable[[], Awaitable[int]]) -> Callable[[], Awaitable[None]]:
    async def ejecutar() -> None:
        try:
            await tarea()
        except Exception:
            # Se registra y se espera a la próxima corrida (la tarea es idempotente).
            registro.exception("Falló la tarea programada %s.", nombre)

    return ejecutar


def programar_reportes(programador: AsyncIOScheduler, tareas: TareasProgramadas, zona: str) -> None:
    """Agrega las tareas de `reportes` al programador del worker (402 y 403 agregan las suyas)."""
    for nombre, hora, minuto in HORARIO:
        programador.add_job(
            _seguro(nombre, getattr(tareas, nombre)),
            CronTrigger(hour=hora, minute=minuto, timezone=ZoneInfo(zona)),
            id=nombre,
            name=nombre,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
            replace_existing=True,
        )


def crear_programador(zona: str) -> AsyncIOScheduler:
    return AsyncIOScheduler(timezone=ZoneInfo(zona))
