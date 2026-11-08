"""Los agentes dentro de `ia-worker` (502): bucle de la cola (revisa cada 2 s), trabajo diario en el
`AsyncIOScheduler` y armado de sus dependencias (modelo de lenguaje, cliente MCP con el token de `ia-agentes`)."""

import asyncio
import logging
from contextlib import suppress
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pymongo.asynchronous.database import AsyncDatabase

from ia.agentes.aplicacion.ejecutor import EjecutorAgente
from ia.agentes.aplicacion.trabajo import TrabajoAgentes
from ia.agentes.infraestructura.conector_mcp import ConectorMcpReal
from ia.agentes.infraestructura.repositorio_mongo import RepositorioEjecucionesMongo, RepositorioSugerenciasMongo
from ia.comun.tiempo import Reloj
from ia.config import ConfiguracionWorker
from ia.lenguaje.aplicacion.puerto import ModeloLenguaje
from ia.mcp.cliente.catalogo import urls_servidores
from ia.mcp.cliente.token import TokenAgentes

registro = logging.getLogger("ia.agentes")

ESPERA_COLA_S = 2.0


def crear_trabajo(
    config: ConfiguracionWorker,
    ia_db: AsyncDatabase[dict[str, Any]],
    modelo: ModeloLenguaje,
    reloj: Reloj,
    http: httpx.AsyncClient,
) -> TrabajoAgentes:
    token = TokenAgentes(
        http, config.token_url, config.agentes_client_id, config.agentes_client_secret.get_secret_value()
    )
    ejecuciones = RepositorioEjecucionesMongo(ia_db)
    ejecutor = EjecutorAgente(
        modelo,
        ConectorMcpReal(urls_servidores(config), token),
        ejecuciones,
        RepositorioSugerenciasMongo(ia_db),
        reloj,
    )
    return TrabajoAgentes(ejecuciones, ejecutor, reloj, modelo.proveedor, modelo.modelo, config.zona_horaria)


async def bucle_cola(trabajo: TrabajoAgentes, detener: asyncio.Event, espera_s: float = ESPERA_COLA_S) -> None:
    """Una ejecución a la vez; sin pendientes, espera `espera_s` (o hasta que se pida detener)."""
    while not detener.is_set():
        try:
            procesada = await trabajo.procesar_siguiente()
        except Exception:
            registro.exception("Falló la cola de los agentes; se reintenta en %s s.", espera_s)
            procesada = False
        if not procesada:
            with suppress(TimeoutError):
                await asyncio.wait_for(detener.wait(), timeout=espera_s)


def programar_agentes(programador: AsyncIOScheduler, trabajo: TrabajoAgentes, cron: str, zona: str) -> None:
    """Encola los tres agentes según `IA_AGENTES_CRON` (por defecto 06:00 de La Paz)."""

    async def diario() -> None:
        try:
            encolados = await trabajo.encolar_diarios()
            registro.info("Agentes encolados para hoy: %s.", ", ".join(encolados) or "ninguno")
        except Exception:
            registro.exception("Falló la ejecución diaria de los agentes.")

    programador.add_job(
        diario,
        CronTrigger.from_crontab(cron, timezone=ZoneInfo(zona)),
        id="agentes_diario",
        name="agentes_diario",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
        replace_existing=True,
    )
