"""`ia-carga-inicial`: llena la base de reportes con lo que ya tienen Obras, Materiales y Finanzas, y termina.

Se corre una vez después de `alembic upgrade head` (y cuando se quiera: es idempotente), con los tres servicios
levantados. Código de salida 0 si la carga terminó bien; 1 si falló o si ya había otra en curso.
"""

import asyncio
import logging
import sys
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.comun.base_datos import crear_motor
from ia.comun.bucle import ejecutar_async
from ia.comun.mongo import asegurar_indices, crear_cliente_mongo
from ia.comun.registros import configurar_registros, id_peticion
from ia.comun.tiempo import Reloj, RelojSistema
from ia.config import ConfiguracionCarga
from ia.reportes.aplicacion.carga_inicial import CargaEnCurso, CargaInicial
from ia.reportes.dominio.valores import CARGA_TERMINADA
from ia.reportes.infraestructura.clientes_rest import (
    ClienteServicio,
    Espera,
    FuenteFinanzasRest,
    FuenteMaterialesRest,
    FuenteObrasRest,
    TokenServicio,
)
from ia.reportes.infraestructura.repositorio_sql import BaseReportesSql
from ia.riesgo.infraestructura.componer import crear_servicio_riesgo

registro = logging.getLogger("ia.carga_inicial")


async def principal(
    config: ConfiguracionCarga,
    *,
    transporte: httpx.AsyncBaseTransport | None = None,
    reloj: Reloj | None = None,
    espera: Espera = asyncio.sleep,
    carga_id: str | None = None,
) -> int:
    carga_id = carga_id or str(uuid.uuid4())
    marca = id_peticion.set(carga_id)
    secreto = config.reportes_client_secret.get_secret_value()
    if not secreto:
        registro.error("Falta IA_REPORTES_CLIENT_SECRET (secreto del cliente ia-reportes en Keycloak).")
        return 1
    reloj = reloj or RelojSistema(config.zona_horaria)
    motor = crear_motor(config.reportes_db_url, tamano_pool=2)
    try:
        async with httpx.AsyncClient(transport=transporte) as http:
            token = TokenServicio(http, config.token_url, config.reportes_client_id, secreto)

            def cliente(nombre: str, url: str) -> ClienteServicio:
                return ClienteServicio(nombre, url, http, token, request_id=carga_id, espera=espera)

            carga = CargaInicial(
                BaseReportesSql(motor),
                FuenteObrasRest(cliente("Obras", config.obras_url)),
                FuenteMaterialesRest(cliente("Materiales", config.materiales_url)),
                FuenteFinanzasRest(cliente("Finanzas", config.finanzas_url)),
                reloj,
            )
            try:
                resultado = await carga.ejecutar(carga_id)
            except CargaEnCurso as error:
                registro.error("%s.", error)
                return 1
        await _predecir_todas(config, motor, reloj, carga_id)
    finally:
        await motor.dispose()
        id_peticion.reset(marca)

    if resultado.estado == CARGA_TERMINADA:
        registro.info("Carga inicial %s TERMINADA: %s obras, filas %s.", carga_id, resultado.obras, resultado.filas)
        return 0
    registro.error("Carga inicial %s FALLIDA: %s", carga_id, resultado.error)
    return 1


async def _predecir_todas(config: ConfiguracionCarga, motor: AsyncEngine, reloj: Reloj, carga_id: str) -> None:
    """402: al terminar la carga, predicción de todas las obras (motivo CARGA_INICIAL). Un fallo aquí se registra y
    no cambia el resultado de la carga (lo corrige la corrida diaria)."""
    mongo = crear_cliente_mongo(config.mongo_url)
    try:
        ia_db = mongo[config.mongo_db]
        await asegurar_indices(ia_db)
        calculadas = await crear_servicio_riesgo(config, motor, ia_db, reloj).tras_carga_inicial(carga_id)
        registro.info("Riesgo de retraso calculado para %s obras (CARGA_INICIAL).", calculadas)
    except Exception:
        registro.exception("No se pudo calcular el riesgo de retraso después de la carga inicial.")
    finally:
        await mongo.close()


def ejecutar() -> None:
    config = ConfiguracionCarga()
    configurar_registros(json_=config.registros_json)
    sys.exit(ejecutar_async(principal(config)))


if __name__ == "__main__":
    ejecutar()
