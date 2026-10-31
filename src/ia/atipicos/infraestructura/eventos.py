"""Engancha la detección de consumos atípicos al manejador de `MovimientoRegistrado` de la 401.

`ConsumidorMensajes.agregar_previo` corre **antes** de la transacción de PostgreSQL de la 401 (inbox, `rpt_movimiento`,
`rpt_consumo_material`); si el paso falla, el mensaje se reintenta como cualquier fallo. Con un `NotificadorRiesgo`
(402), `agregar_posterior` agrega el recálculo del riesgo después de confirmarla.
"""

from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.atipicos.aplicacion.deteccion import DetectarConsumoAtipico, RecalcularRiesgoTrasAtipico
from ia.atipicos.aplicacion.puertos import ModeloAtipicos, NotificadorRiesgo
from ia.atipicos.infraestructura.repositorio_mongo import Documento, RepositorioAtipicosMongo
from ia.atipicos.infraestructura.salidas_sql import EventosAplicadosSql, HistorialSalidasSql
from ia.comun.tiempo import Reloj
from ia.mensajeria.consumidor import ConsumidorMensajes
from ia.mensajeria.sobre import MensajeInvalidoError, Sobre
from ia.reportes.aplicacion.lecturas import MovimientoLeido
from ia.reportes.infraestructura.campos import DatoInvalido
from ia.reportes.infraestructura.eventos import MOVIMIENTO_REGISTRADO, leer_movimiento

TIPO_MOVIMIENTO = MOVIMIENTO_REGISTRADO.rsplit(".", 1)[-1]


def registrar_deteccion(
    consumidor: ConsumidorMensajes,
    motor: AsyncEngine,
    ia_db: AsyncDatabase[Documento],
    reloj: Reloj,
    modelo: ModeloAtipicos | None,
    zona_horaria: str = "America/La_Paz",
    notificador: NotificadorRiesgo | None = None,
) -> DetectarConsumoAtipico:
    """Agrega los pasos a `MovimientoRegistrado` (los manejadores de la 401 ya deben estar registrados).

    `notificador`: el recálculo del riesgo de la 402; sin él (402 no implementada) el paso posterior no se registra.
    """
    repositorio = RepositorioAtipicosMongo(ia_db)
    deteccion = DetectarConsumoAtipico(
        modelo=modelo,
        historial=HistorialSalidasSql(motor),
        eventos=EventosAplicadosSql(motor),
        repositorio=repositorio,
        reloj=reloj,
        zona=zona_horaria,
    )

    async def evaluar_salida(sobre: Sobre) -> None:
        await deteccion.evaluar(sobre.id, _movimiento(sobre))

    consumidor.agregar_previo(TIPO_MOVIMIENTO, evaluar_salida)

    if notificador is not None:
        recalculo = RecalcularRiesgoTrasAtipico(repositorio, notificador)

        async def recalcular_riesgo(sobre: Sobre, aplicado: bool) -> None:
            await recalculo.despues_de_aplicar(_movimiento(sobre), aplicado)

        consumidor.agregar_posterior(TIPO_MOVIMIENTO, recalcular_riesgo)
    return deteccion


def _movimiento(sobre: Sobre) -> MovimientoLeido:
    try:
        return leer_movimiento(sobre.datos)
    except DatoInvalido as error:
        raise MensajeInvalidoError(str(error)) from error
