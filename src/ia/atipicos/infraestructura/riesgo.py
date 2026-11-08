"""Adaptador del recálculo del riesgo de retraso (402) tras un consumo atípico (403, paso 5 de la detección)."""

from ia.riesgo.aplicacion.puertos import CONSUMO_ATIPICO
from ia.riesgo.aplicacion.recalcular import ServicioRiesgo, clave_evento


class RecalculoRiesgo402:
    """`NotificadorRiesgo` con `ServicioRiesgo.recalcular_obra` (idempotente por la clave `{obraId}:{eventoId}`)."""

    def __init__(self, servicio: ServicioRiesgo) -> None:
        self._servicio = servicio

    async def consumo_atipico(self, obra_id: str, evento_id: str) -> None:
        await self._servicio.recalcular_obra(obra_id, motivo=CONSUMO_ATIPICO, clave=clave_evento(obra_id, evento_id))
