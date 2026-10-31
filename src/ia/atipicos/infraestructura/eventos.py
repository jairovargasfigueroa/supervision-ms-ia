"""Engancha la detección de consumos atípicos al manejador de `MovimientoRegistrado` de la 401.

`ConsumidorMensajes.agregar_previo` corre **antes** de la transacción de PostgreSQL de la 401 (inbox, `rpt_movimiento`,
`rpt_consumo_material`); si el paso falla, el mensaje se reintenta como cualquier fallo.
"""

from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.atipicos.aplicacion.deteccion import DetectarConsumoAtipico
from ia.atipicos.aplicacion.puertos import ModeloAtipicos
from ia.atipicos.infraestructura.repositorio_mongo import Documento, RepositorioAtipicosMongo
from ia.atipicos.infraestructura.salidas_sql import EventosAplicadosSql, HistorialSalidasSql
from ia.comun.tiempo import Reloj
from ia.mensajeria.consumidor import ConsumidorMensajes
from ia.mensajeria.sobre import MensajeInvalidoError, Sobre
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
) -> DetectarConsumoAtipico:
    """Agrega el paso previo a `MovimientoRegistrado` (los manejadores de la 401 ya deben estar registrados)."""
    deteccion = DetectarConsumoAtipico(
        modelo=modelo,
        historial=HistorialSalidasSql(motor),
        eventos=EventosAplicadosSql(motor),
        repositorio=RepositorioAtipicosMongo(ia_db),
        reloj=reloj,
        zona=zona_horaria,
    )

    async def evaluar_salida(sobre: Sobre) -> None:
        try:
            movimiento = leer_movimiento(sobre.datos)
        except DatoInvalido as error:
            raise MensajeInvalidoError(str(error)) from error
        await deteccion.evaluar(sobre.id, movimiento)

    consumidor.agregar_previo(TIPO_MOVIMIENTO, evaluar_salida)
    return deteccion
