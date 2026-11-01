"""`ia-worker`: consumidor de la cola `ia` y tareas programadas."""

import asyncio
import logging
import signal
from contextlib import suppress
from pathlib import Path

import httpx

from ia.agentes.infraestructura.worker import bucle_cola, crear_trabajo, programar_agentes
from ia.atipicos.infraestructura.eventos import registrar_deteccion
from ia.atipicos.infraestructura.modelo import cargar_modelo_o_nada
from ia.atipicos.infraestructura.riesgo import RecalculoRiesgo402
from ia.comun.base_datos import crear_motor
from ia.comun.bucle import ejecutar_async
from ia.comun.mongo import asegurar_indices_al_arrancar, crear_cliente_mongo
from ia.comun.registros import configurar_registros
from ia.comun.tiempo import RelojSistema
from ia.config import ConfiguracionInvalida, ConfiguracionWorker
from ia.lenguaje.infraestructura.componer import crear_modelo_lenguaje
from ia.mensajeria.consumidor import ConsumidorMensajes
from ia.mensajeria.rabbit import ConexionRabbit
from ia.reportes.aplicacion.tareas_programadas import TareasProgramadas
from ia.reportes.infraestructura.eventos import registrar_manejadores
from ia.reportes.infraestructura.programador import crear_programador, programar_reportes
from ia.reportes.infraestructura.repositorio_sql import BaseReportesSql
from ia.riesgo.infraestructura.componer import crear_servicio_riesgo
from ia.riesgo.infraestructura.disparadores import programar_riesgo, registrar_disparadores

registro = logging.getLogger("ia.worker")


async def principal(config: ConfiguracionWorker) -> None:
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
    riesgo = crear_servicio_riesgo(config, motor, mongo[config.mongo_db], reloj)
    registrar_disparadores(consumidor, riesgo)
    registrar_deteccion(
        consumidor,
        motor,
        mongo[config.mongo_db],
        reloj,
        cargar_modelo_o_nada(config.modelo_atipicos_version, Path(config.modelos_dir) / "consumos_atipicos"),
        config.zona_horaria,
        notificador=RecalculoRiesgo402(riesgo),
    )
    await asegurar_indices_al_arrancar(mongo[config.mongo_db])

    programador = crear_programador(config.zona_horaria)
    programar_reportes(programador, TareasProgramadas(BaseReportesSql(motor), reloj), config.zona_horaria)
    programar_riesgo(programador, riesgo, config.zona_horaria)

    # 502: host de los agentes (modelo de lenguaje + cliente MCP con el token de ia-agentes).
    http_token = httpx.AsyncClient()
    agentes = crear_trabajo(config, mongo[config.mongo_db], crear_modelo_lenguaje(config), reloj, http_token)
    try:
        await agentes.recuperar()
    except Exception:
        registro.exception("No se pudieron recuperar las ejecuciones de agentes interrumpidas.")
    if config.agentes_programados_activos:
        programar_agentes(programador, agentes, config.agentes_cron, config.zona_horaria)
    cola_agentes = asyncio.create_task(bucle_cola(agentes, detener))

    conexion = ConexionRabbit(config.rabbitmq_url, consumidor)
    try:
        programador.start()
        if await conexion.iniciar(detener):
            registro.info("ia-worker en marcha.")
            await detener.wait()
    finally:
        detener.set()
        if programador.running:
            programador.shutdown(wait=False)
        cola_agentes.cancel()
        with suppress(asyncio.CancelledError):
            await cola_agentes
        await http_token.aclose()
        await conexion.cerrar()
        await mongo.close()
        await motor.dispose()
        registro.info("ia-worker detenido.")


def ejecutar() -> None:
    config = ConfiguracionWorker()
    configurar_registros(json_=config.registros_json)
    try:
        config.comprobar_llm()
    except ConfiguracionInvalida as error:
        registro.error("ia-worker no arranca: %s", error)
        raise SystemExit(1) from error
    with suppress(KeyboardInterrupt):
        ejecutar_async(principal(config))


if __name__ == "__main__":
    ejecutar()
