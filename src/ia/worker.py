"""`ia-worker`: consumidor de la cola `ia` y tareas programadas."""

import asyncio
import logging
import signal
from contextlib import suppress

from ia.atipicos.infraestructura.eventos import registrar_deteccion
from ia.atipicos.infraestructura.modelo import cargar_modelo_o_nada
from ia.comun.base_datos import crear_motor
from ia.comun.bucle import ejecutar_async
from ia.comun.mongo import asegurar_indices, crear_cliente_mongo, indices_registrados
from ia.comun.registros import configurar_registros
from ia.comun.tiempo import RelojSistema
from ia.config import Configuracion
from ia.mensajeria.consumidor import ConsumidorMensajes
from ia.mensajeria.rabbit import ConexionRabbit
from ia.reportes.aplicacion.tareas_programadas import TareasProgramadas
from ia.reportes.infraestructura.eventos import registrar_manejadores
from ia.reportes.infraestructura.programador import crear_programador, programar_reportes
from ia.reportes.infraestructura.repositorio_sql import BaseReportesSql

registro = logging.getLogger("ia.worker")


async def principal(config: Configuracion) -> None:
    detener = asyncio.Event()
    for senal in (signal.SIGINT, signal.SIGTERM):
        # En Windows no hay manejadores de señales en el bucle: Ctrl+C llega como KeyboardInterrupt.
        with suppress(NotImplementedError, RuntimeError):
            asyncio.get_running_loop().add_signal_handler(senal, detener.set)

    motor = crear_motor(config.reportes_db_url)
    mongo = crear_cliente_mongo(config.mongo_url)
    reloj = RelojSistema(config.zona_horaria)
    consumidor = ConsumidorMensajes()
    registrar_manejadores(consumidor, motor, reloj, config.zona_horaria)
    registrar_deteccion(
        consumidor,
        motor,
        mongo[config.mongo_db],
        reloj,
        cargar_modelo_o_nada(config.modelo_atipicos_version),
        config.zona_horaria,
    )
    if indices_registrados():
        await asegurar_indices(mongo[config.mongo_db])

    programador = crear_programador(config.zona_horaria)
    programar_reportes(programador, TareasProgramadas(BaseReportesSql(motor), reloj), config.zona_horaria)

    conexion = ConexionRabbit(config.rabbitmq_url, consumidor)
    try:
        programador.start()
        if await conexion.iniciar(detener):
            registro.info("ia-worker en marcha.")
            await detener.wait()
    finally:
        if programador.running:
            programador.shutdown(wait=False)
        await conexion.cerrar()
        await mongo.close()
        await motor.dispose()
        registro.info("ia-worker detenido.")


def ejecutar() -> None:
    config = Configuracion()
    configurar_registros(json_=config.registros_json)
    with suppress(KeyboardInterrupt):
        ejecutar_async(principal(config))


if __name__ == "__main__":
    ejecutar()
