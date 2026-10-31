"""Recálculo del riesgo de retraso (criterio 4): de una obra (tras un evento) o de todas (diario y carga inicial)."""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

from ia.comun.tiempo import Reloj
from ia.riesgo.aplicacion.prediccion import calcular_prediccion
from ia.riesgo.aplicacion.puertos import (
    CARGA_INICIAL,
    DIARIO,
    AlmacenPredicciones,
    ContadorAtipicos,
    FuenteRiesgo,
    ModeloRiesgo,
    Prediccion,
)

registro = logging.getLogger("ia.riesgo")

DIAS_ATIPICOS = 90


def clave_evento(obra_id: str, evento_id: str) -> str:
    """Clave de idempotencia de un recálculo disparado por un evento: `{obraId}:{eventoId}`."""
    return f"{obra_id}:{evento_id}"


class ServicioRiesgo:
    def __init__(
        self,
        fuente: FuenteRiesgo,
        atipicos: ContadorAtipicos,
        almacen: AlmacenPredicciones,
        modelo: ModeloRiesgo | None,
        reloj: Reloj,
    ) -> None:
        self._fuente = fuente
        self._atipicos = atipicos
        self._almacen = almacen
        self._modelo = modelo
        self._reloj = reloj

    @property
    def modelo(self) -> ModeloRiesgo | None:
        return self._modelo

    async def recalcular_obra(self, obra_id: str, motivo: str, clave: str) -> Prediccion | None:
        """Calcula y guarda la predicción de la obra (idempotente por `clave`) y actualiza `rpt_obra_resumen`.

        Devuelve `None` si no hay modelo o la obra no está en la base de reportes. Es la función pública que usan
        los disparadores de eventos, la tarea diaria, la carga inicial y (403) el consumo atípico.
        """
        if self._modelo is None:
            registro.error("Sin modelo de riesgo de retraso: no se calcula la obra %s (%s).", obra_id, motivo)
            return None
        hoy = self._reloj.hoy()
        ahora = self._reloj.ahora()
        obra = await self._fuente.leer_obra(obra_id, hoy)
        if obra is None:
            return None
        if obra.estado_obra is not None:
            desde = ahora - timedelta(days=DIAS_ATIPICOS)
            obra = replace(
                obra, estado_obra=replace(obra.estado_obra, atipicos_90d=await self._atipicos.contar(obra_id, desde))
            )
        prediccion = calcular_prediccion(self._modelo, obra, hoy, motivo, clave, ahora.replace(microsecond=0))
        if await self._almacen.guardar(prediccion):
            await self._fuente.guardar_riesgo(obra_id, prediccion.riesgo_retraso, prediccion.nivel, ahora)
            registro.info(
                "Riesgo de la obra %s (%s): %s %s", obra_id, motivo, prediccion.nivel, prediccion.riesgo_retraso
            )
        return prediccion

    async def diario(self) -> int:
        """01:00 · todas las obras `EN_EJECUCION`, clave `{obraId}:diario:{AAAA-MM-DD}`."""
        hoy = self._reloj.hoy().isoformat()
        return await self._todas(DIARIO, lambda obra_id: f"{obra_id}:diario:{hoy}", solo_en_ejecucion=True)

    async def tras_carga_inicial(self, carga_id: str) -> int:
        """Al terminar la carga inicial (401): todas las obras de la base, clave `{obraId}:carga:{cargaId}`."""
        return await self._todas(CARGA_INICIAL, lambda obra_id: f"{obra_id}:carga:{carga_id}", solo_en_ejecucion=False)

    async def _todas(self, motivo: str, clave: Callable[[str], str], solo_en_ejecucion: bool) -> int:
        if self._modelo is None:
            registro.error("Sin modelo de riesgo de retraso: no se calcula ninguna obra (%s).", motivo)
            return 0
        calculadas = 0
        for obra_id in await self._fuente.obras(solo_en_ejecucion):
            try:
                if await self.recalcular_obra(obra_id, motivo, clave(obra_id)) is not None:
                    calculadas += 1
            except Exception:
                # Se registra y se sigue con las demás; la siguiente corrida diaria lo corrige.
                registro.exception("Falló el riesgo de la obra %s (%s).", obra_id, motivo)
        return calculadas
