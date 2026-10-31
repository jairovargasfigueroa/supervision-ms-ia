"""Tareas diarias del modelo de lectura (zona del negocio): vencer cobros y recalcular el plazo transcurrido."""

import logging

from ia.comun.tiempo import Reloj
from ia.reportes.aplicacion.puertos import BaseReportes
from ia.reportes.dominio import indicadores
from ia.reportes.dominio.valores import VENCIDO

registro = logging.getLogger("ia.reportes.tareas")


class TareasProgramadas:
    def __init__(self, base: BaseReportes, reloj: Reloj) -> None:
        self._base = base
        self._reloj = reloj

    async def vencer_cobros(self) -> int:
        """00:20 · cobros `PENDIENTE` con `vence_en` anterior a hoy → `VENCIDO` (criterio 9). Devuelve cuántos."""
        hoy = self._reloj.hoy()
        async with self._base.transaccion() as repo:
            pendientes = await repo.cobros_pendientes()
            vencidos = [c.cobro_id for c in pendientes if indicadores.estado_cobro(c.vence_en, hoy) == VENCIDO]
            cambiados = await repo.marcar_vencidos(vencidos, self._reloj.ahora())
        registro.info("Cobros vencidos el %s: %s", hoy.isoformat(), cambiados)
        return cambiados

    async def recalcular_plazos(self) -> int:
        """00:30 · `plazo_pct` de todas las obras con la fecha de hoy (criterio 10). Devuelve cuántas obras."""
        hoy = self._reloj.hoy()
        async with self._base.transaccion() as repo:
            obras = await repo.obras_para_plazo()
            for obra in obras:
                plazo = (
                    indicadores.plazo_pct(obra.fecha_inicio, obra.plazo_dias, hoy)
                    if obra.fecha_inicio is not None and obra.plazo_dias
                    else None
                )
                if plazo is not None:
                    await repo.actualizar_indicadores(obra.obra_id, self._reloj.ahora(), plazo_pct=plazo)
        registro.info("Plazo transcurrido recalculado el %s: %s obras", hoy.isoformat(), len(obras))
        return len(obras)
