"""Detección de consumos atípicos al aplicar `MovimientoRegistrado` (403, diseño «Detección en el servicio»).

Corre como paso **previo** de la 401 (antes de su transacción de PostgreSQL), en este orden:

1. lee el historial anterior de `rpt_movimiento` (sin la propia salida, aunque la haya traído la carga inicial) y
   calcula las variables;
2. evalúa con el modelo; sin modelo, registra el error y sigue sin detectar;
3. si es atípica, guarda el consumo atípico con `_id = movimientoId` y `$setOnInsert` (idempotente).

Después corre la transacción de la 401 (paso 4). Un evento ya aplicado (está en `evento_aplicado`) se confirma sin
evaluar; un reintento repite los pasos 1 a 3 sin duplicar.

Paso 5, **posterior** (después de confirmar, solo si el evento se aplicó ahora): si la salida es un consumo atípico,
pide a la 402 recalcular el riesgo de retraso de la obra (motivo `CONSUMO_ATIPICO`). Sin notificador, el paso no
existe.
"""

import asyncio
import logging
from dataclasses import dataclass

from ia.atipicos.aplicacion.puertos import (
    EventosAplicados,
    FuenteHistorial,
    ModeloAtipicos,
    NotificadorRiesgo,
    RepositorioAtipicos,
)
from ia.atipicos.dominio.consumo import ConsumoAtipico
from ia.atipicos.dominio.variables import SalidaMaterial, calcular_variables_salida
from ia.comun.tiempo import Reloj
from ia.reportes.aplicacion.lecturas import MovimientoLeido
from ia.reportes.dominio.valores import SALIDA

registro = logging.getLogger("ia.atipicos")


@dataclass(frozen=True)
class ResultadoDeteccion:
    evaluada: bool
    atipica: bool = False
    guardada: bool = False
    """`True` si se insertó ahora (en un reintento, el documento ya podía existir)."""


class DetectarConsumoAtipico:
    def __init__(
        self,
        modelo: ModeloAtipicos | None,
        historial: FuenteHistorial,
        eventos: EventosAplicados,
        repositorio: RepositorioAtipicos,
        reloj: Reloj,
        zona: str = "America/La_Paz",
    ) -> None:
        self._modelo = modelo
        self._historial = historial
        self._eventos = eventos
        self._repositorio = repositorio
        self._reloj = reloj
        self._zona = zona

    async def evaluar(self, evento_id: str, movimiento: MovimientoLeido) -> ResultadoDeteccion:
        if movimiento.tipo != SALIDA or movimiento.partida_id is None:
            return ResultadoDeteccion(evaluada=False)  # Los ingresos no se evalúan (criterio 2).
        if await self._eventos.ya_aplicado(evento_id):
            return ResultadoDeteccion(evaluada=False)
        if self._modelo is None:
            registro.error(
                "Salida %s sin evaluar: no hay modelo de consumos atípicos cargado.", movimiento.movimiento_id
            )
            return ResultadoDeteccion(evaluada=False)

        salida = SalidaMaterial(
            movimiento_id=movimiento.movimiento_id,
            obra_id=movimiento.obra_id,
            material_id=movimiento.material_id,
            cantidad=float(movimiento.cantidad),
            periodo=movimiento.periodo,
            supera_stock=movimiento.supera_stock,
            registrado_en=movimiento.registrado_en,
        )
        variables = calcular_variables_salida(salida, await self._historial.historial_de(salida), self._zona)
        # El Isolation Forest es CPU (unos milisegundos): fuera del bucle para no frenar los otros mensajes.
        evaluacion = await asyncio.to_thread(self._modelo.evaluar, variables)
        if not evaluacion.atipica:
            return ResultadoDeteccion(evaluada=True)

        consumo = ConsumoAtipico(
            id=movimiento.movimiento_id,
            obra_id=movimiento.obra_id,
            material_id=movimiento.material_id,
            partida_id=movimiento.partida_id,
            cantidad=movimiento.cantidad,
            periodo=movimiento.periodo,
            registrado_en=movimiento.registrado_en,
            puntaje=evaluacion.puntaje,
            motivos=tuple(evaluacion.motivos),
            variables=variables.como_dict(),
            version_modelo=evaluacion.version,
            detectado_en=self._reloj.ahora(),
        )
        guardada = await self._repositorio.guardar_si_no_existe(consumo)
        registro.info(
            "Consumo atípico %s (obra %s, puntaje %.3f)%s.",
            consumo.id,
            consumo.obra_id,
            consumo.puntaje,
            "" if guardada else ", ya estaba guardado",
        )
        return ResultadoDeteccion(evaluada=True, atipica=True, guardada=guardada)


class RecalcularRiesgoTrasAtipico:
    """Paso 5: después de confirmar la transacción de la 401, avisa a la 402 si la salida quedó como atípica."""

    def __init__(self, repositorio: RepositorioAtipicos, notificador: NotificadorRiesgo) -> None:
        self._repositorio = repositorio
        self._notificador = notificador

    async def despues_de_aplicar(self, evento_id: str, movimiento: MovimientoLeido, aplicado: bool) -> bool:
        """`True` si pidió el recálculo.

        Si el recálculo falla, se registra y no se reintenta el mensaje: el evento ya quedó aplicado (un reintento no
        volvería a llegar a este paso) y la tarea diaria de la 402 recalcula todas las obras.
        """
        if not aplicado or movimiento.tipo != SALIDA:
            return False
        consumo = await self._repositorio.obtener(movimiento.movimiento_id)
        if consumo is None:
            return False
        try:
            await self._notificador.consumo_atipico(consumo.obra_id, evento_id)
        except Exception:
            registro.exception(
                "No se pudo recalcular el riesgo de la obra %s tras un consumo atípico.", consumo.obra_id
            )
            return False
        return True
