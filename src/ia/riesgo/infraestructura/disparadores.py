"""Disparadores del riesgo en el worker y la tarea diaria de la 01:00 (criterio 4).

Se calculan **después** de confirmar la transacción del evento (pasos posteriores del consumidor). Si el cálculo
falla, se registra y lo corrige la corrida diaria: el evento no se reintenta por esto.
"""

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ia.mensajeria.consumidor import ConsumidorMensajes, PasoPosterior
from ia.mensajeria.sobre import Sobre
from ia.riesgo.aplicacion.puertos import CONTRATO_REGISTRADO, PLANILLA_APROBADA, PLANILLA_OBSERVADA
from ia.riesgo.aplicacion.recalcular import ServicioRiesgo, clave_evento

registro = logging.getLogger("ia.riesgo.disparadores")

# Tipo de evento (ya registrado por `reportes`) → motivo del cálculo.
MOTIVOS = {
    "PlanillaAprobada": PLANILLA_APROBADA,
    "PlanillaObservada": PLANILLA_OBSERVADA,
    "ContratoRegistrado": CONTRATO_REGISTRADO,
}
HORA_DIARIA = (1, 0)


def paso_riesgo(servicio: ServicioRiesgo, motivo: str) -> PasoPosterior:
    async def recalcular(sobre: Sobre, _aplicado: bool) -> None:
        # Corre también con un evento repetido: la clave lo hace idempotente y completa un cálculo que falló.
        obra_id = sobre.datos.get("obraId")
        if not isinstance(obra_id, str):
            return
        try:
            await servicio.recalcular_obra(obra_id, motivo, clave_evento(obra_id, sobre.id))
        except Exception:
            registro.exception(
                "Falló el riesgo de la obra %s tras %s (lo corrige la corrida diaria).", obra_id, sobre.tipo
            )

    return recalcular


def registrar_disparadores(consumidor: ConsumidorMensajes, servicio: ServicioRiesgo) -> None:
    for tipo, motivo in MOTIVOS.items():
        consumidor.agregar_posterior(tipo, paso_riesgo(servicio, motivo))


def programar_riesgo(programador: AsyncIOScheduler, servicio: ServicioRiesgo, zona: str) -> None:
    async def diario() -> None:
        try:
            calculadas = await servicio.diario()
            registro.info("Riesgo diario: %s obras.", calculadas)
        except Exception:
            registro.exception("Falló la tarea diaria del riesgo de retraso.")

    hora, minuto = HORA_DIARIA
    programador.add_job(
        diario,
        CronTrigger(hour=hora, minute=minuto, timezone=ZoneInfo(zona)),
        id="riesgo_diario",
        name="riesgo_diario",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
        replace_existing=True,
    )
