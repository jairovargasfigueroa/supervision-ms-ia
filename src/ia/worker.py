"""`ia-worker`: consumidor de la cola `ia` y tareas programadas."""

import asyncio
import logging
import signal
from contextlib import suppress

from ia.comun.base_datos import crear_motor
from ia.comun.bucle import ejecutar_async
from ia.comun.mongo import asegurar_indices, crear_cliente_mongo, indices_registrados
from ia.comun.registros import configurar_registros
from ia.config import Configuracion
from ia.mensajeria.consumidor import ConsumidorMensajes
from ia.mensajeria.rabbit import ConexionRabbit

registro = logging.getLogger("ia.worker")


async def principal(config: Configuracion) -> None:
    detener = asyncio.Event()
    for senal in (signal.SIGINT, signal.SIGTERM):
        # En Windows no hay manejadores de señales en el bucle: Ctrl+C llega como KeyboardInterrupt.
        with suppress(NotImplementedError, RuntimeError):
            asyncio.get_running_loop().add_signal_handler(senal, detener.set)

    motor = crear_motor(config.reportes_db_url)
    mongo = crear_cliente_mongo(config.mongo_url)
    consumidor = ConsumidorMensajes()
    if indices_registrados():
        await asegurar_indices(mongo[config.mongo_db])

    conexion = ConexionRabbit(config.rabbitmq_url, consumidor)
    try:
        if await conexion.iniciar(detener):
            registro.info("ia-worker en marcha.")
            await detener.wait()
    finally:
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
