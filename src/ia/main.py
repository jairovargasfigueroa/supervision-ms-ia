"""`ia-api`: FastAPI con Uvicorn en el puerto 8085 (atiende al gateway)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.auth.dependencias import autenticar_token
from ia.auth.token import ProveedorJwks, ValidadorToken
from ia.comun.base_datos import crear_motor
from ia.comun.bucle import ejecutar_async
from ia.comun.http import instalar_http
from ia.comun.mongo import asegurar_indices, crear_cliente_mongo, indices_registrados
from ia.comun.registros import configurar_registros
from ia.config import Configuracion

registro = logging.getLogger("ia.api")


def crear_app(
    config: Configuracion | None = None,
    *,
    validador: ValidadorToken | None = None,
    motor: AsyncEngine | None = None,
) -> FastAPI:
    config = config or Configuracion()

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        # Ninguna de las dos conexiones se abre hasta la primera consulta: /salud no depende de ellas.
        propio = motor is None
        app.state.motor = motor or crear_motor(config.reportes_db_url)
        app.state.mongo = crear_cliente_mongo(config.mongo_url)
        app.state.ia_db = app.state.mongo[config.mongo_db]
        if indices_registrados():
            await asegurar_indices(app.state.ia_db)
        try:
            yield
        finally:
            await app.state.mongo.close()
            if propio:
                await app.state.motor.dispose()

    app = FastAPI(
        lifespan=ciclo_de_vida,
        title="Servicio de IA",
        version="0.1.0",
        docs_url="/docs" if config.entorno == "desarrollo" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if config.entorno == "desarrollo" else None,
    )
    app.state.config = config
    app.state.validador_token = validador or ValidadorToken(
        ProveedorJwks(config.jwks_url), emisor=config.keycloak_issuer, audiencia=config.audiencia
    )
    instalar_http(app, autenticador=autenticar_token)

    @app.get("/salud", tags=["salud"])
    async def salud() -> dict[str, str]:
        """Sin token; no toca la base ni la cola."""
        return {"estado": "ok"}

    return app


def ejecutar() -> None:
    config = Configuracion()
    configurar_registros(json_=config.registros_json)
    servidor = uvicorn.Server(uvicorn.Config(crear_app(config), host="0.0.0.0", port=config.puerto, log_config=None))
    registro.info("ia-api en el puerto %s", config.puerto)
    ejecutar_async(servidor.serve())


if __name__ == "__main__":
    ejecutar()
