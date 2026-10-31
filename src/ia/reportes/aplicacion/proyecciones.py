"""Proyecciones: un caso de uso por evento. Los usan el consumidor de eventos y la carga inicial.

Cada uno recibe el repositorio de una transacción ya abierta (la del inbox, o la de la obra en la carga).
"""

from datetime import date
from decimal import Decimal

from ia.comun.tiempo import Reloj
from ia.reportes.aplicacion.lecturas import (
    CobroEmitidoLeido,
    MovimientoLeido,
    ObraLeida,
    PlanillaAprobadaLeida,
    PlanillaObservadaLeida,
)
from ia.reportes.aplicacion.puertos import CobroParcial, ObraResumen, RepositorioReportes
from ia.reportes.dominio import indicadores
from ia.reportes.dominio.valores import SALIDA


class ObraNoRegistrada(Exception):
    """La obra todavía no está en la base de reportes: el evento falla y se reintenta (con una sola cola,
    `ObraRegistrada` llega antes)."""

    def __init__(self, obra_id: str) -> None:
        super().__init__(f"La obra {obra_id} todavía no está en la base de reportes.")
        self.obra_id = obra_id


class Proyecciones:
    def __init__(self, reloj: Reloj) -> None:
        self._reloj = reloj

    def plazo_de_hoy(self, fecha_inicio: date | None, plazo_dias: int | None) -> Decimal:
        if fecha_inicio is None or not plazo_dias:
            return indicadores.redondear(Decimal(0))
        return indicadores.plazo_pct(fecha_inicio, plazo_dias, self._reloj.hoy())

    async def obra_registrada(self, repo: RepositorioReportes, obra: ObraLeida) -> bool:
        """Inserta la obra (`SIN_CONTRATO`, avance 0, plazo de hoy) o actualiza sus datos descriptivos."""
        plazo = self.plazo_de_hoy(obra.fecha_inicio, obra.plazo_dias)
        return await repo.guardar_obra(obra, plazo, self._reloj.ahora())

    async def contrato_registrado(self, repo: RepositorioReportes, obra_id: str) -> None:
        if not await repo.marcar_en_ejecucion(obra_id, self._reloj.ahora()):
            raise ObraNoRegistrada(obra_id)

    async def planilla_aprobada(self, repo: RepositorioReportes, planilla: PlanillaAprobadaLeida) -> None:
        obra = await repo.bloquear_obra(planilla.obra_id)
        if obra is None:
            raise ObraNoRegistrada(planilla.obra_id)
        ahora = self._reloj.ahora()
        monto = indicadores.monto_periodo(planilla.montos_lineas)
        avance_periodo = indicadores.avance_periodo_pct(monto, obra.monto_contrato or Decimal(0))
        await repo.guardar_planilla_aprobada(planilla, monto, avance_periodo, ahora)
        await self.recalcular_avance(repo, obra)
        if planilla.cobro_id is not None:
            await repo.completar_cobro(
                CobroParcial(
                    cobro_id=planilla.cobro_id,
                    obra_id=planilla.obra_id,
                    planilla_id=planilla.planilla_id,
                    monto=planilla.neto,
                    cliente_id=obra.cliente_id,
                    zona=obra.zona,
                ),
                ahora,
            )

    async def recalcular_avance(self, repo: RepositorioReportes, obra: ObraResumen) -> Decimal:
        """Avance desde todas las planillas `APROBADA` de `rpt_planilla` (nunca se suma al valor anterior)."""
        montos = await repo.montos_aprobados(obra.obra_id)
        avance = indicadores.avance_pct(montos, obra.monto_contrato or Decimal(0))
        await repo.actualizar_indicadores(obra.obra_id, self._reloj.ahora(), avance_pct=avance)
        return avance

    async def planilla_observada(self, repo: RepositorioReportes, observacion: PlanillaObservadaLeida) -> bool:
        """`True` si sumó la observación; `False` si ya estaba contada por la carga inicial."""
        await repo.bloquear_obra(observacion.obra_id)  # si existe: queda en fila con la carga y las aprobaciones
        existente = await repo.obtener_planilla(observacion.planilla_id)
        if (
            existente is not None
            and existente.cargada_en is not None
            and observacion.ocurrida_en is not None
            and observacion.ocurrida_en <= existente.cargada_en
        ):
            return False
        await repo.registrar_observacion(
            observacion.planilla_id,
            observacion.obra_id,
            observacion.motivo,
            observacion.origen,
            self._reloj.ahora(),
        )
        return True

    async def cobro_emitido(self, repo: RepositorioReportes, cobro: CobroEmitidoLeido) -> None:
        await repo.completar_cobro(
            CobroParcial(
                cobro_id=cobro.cobro_id,
                numero=cobro.numero,
                planilla_id=cobro.planilla_id,
                emitido_en=cobro.emitido_en,
                vence_en=cobro.vence_en,
                estado=indicadores.estado_cobro(cobro.vence_en, self._reloj.hoy()),
            ),
            self._reloj.ahora(),
        )

    async def movimiento_registrado(self, repo: RepositorioReportes, movimiento: MovimientoLeido) -> bool:
        """Solo si el movimiento se insertó (no estaba) y es `SALIDA`, suma en el consumo. `True` si se insertó."""
        insertado = await repo.insertar_movimiento(movimiento)
        if insertado and movimiento.tipo == SALIDA and movimiento.partida_id is not None:
            await repo.sumar_consumo(
                movimiento.obra_id,
                movimiento.periodo,
                movimiento.material_id,
                movimiento.partida_id,
                indicadores.redondear_cantidad(movimiento.cantidad),
                indicadores.monto_consumo(movimiento.cantidad, movimiento.precio_referencial),
                self._reloj.ahora(),
            )
        return insertado
