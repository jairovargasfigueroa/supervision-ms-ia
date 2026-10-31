"""`ia-api`: FastAPI con Uvicorn en el puerto 8085 (atiende al gateway)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.atipicos.infraestructura.web import router as rutas_atipicos
from ia.auth.dependencias import autenticar_token
from ia.auth.token import ProveedorJwks, ValidadorToken
from ia.comun.base_datos import crear_motor
from ia.comun.bucle import ejecutar_async
from ia.comun.http import instalar_http
from ia.comun.mongo import asegurar_indices_al_arrancar, crear_cliente_mongo
from ia.comun.registros import configurar_registros
from ia.config import Configuracion
from ia.reportes.infraestructura.web import router as rutas_reportes
from ia.riesgo.aplicacion.puertos import ModeloRiesgo
from ia.riesgo.infraestructura.modelo_xgboost import cargar_modelo
from ia.riesgo.infraestructura.web import router as rutas_riesgo

registro = logging.getLogger("ia.api")


def crear_app(
    config: Configuracion | None = None,
    *,
    validador: ValidadorToken | None = None,
    motor: AsyncEngine | None = None,
    modelo_riesgo: ModeloRiesgo | None = None,
    cargar_modelo_riesgo: bool = True,
) -> FastAPI:
    config = config or Configuracion()

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI) -> AsyncIterator[None]:
        # Ninguna de las dos conexiones se abre hasta la primera consulta: /salud no depende de ellas.
        propio = motor is None
        app.state.motor = motor or crear_motor(config.reportes_db_url)
        app.state.mongo = crear_cliente_mongo(config.mongo_url)
        app.state.ia_db = app.state.mongo[config.mongo_db]
        await asegurar_indices_al_arrancar(app.state.ia_db)
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
    # 402: el modelo se carga una vez; sin modelo, GET /modelos/riesgo-retraso responde 503 MODELO_NO_DISPONIBLE.
    app.state.modelo_riesgo = modelo_riesgo or (
        cargar_modelo(config.modelos_dir, config.modelo_riesgo_version) if cargar_modelo_riesgo else None
    )
    app.state.validador_token = validador or ValidadorToken(
        ProveedorJwks(config.jwks_url), emisor=config.keycloak_issuer, audiencia=config.audiencia
    )
    instalar_http(app, autenticador=autenticar_token)

    @app.get("/salud", tags=["salud"])
    async def salud() -> dict[str, str]:
        """Sin token; no toca la base ni la cola."""
        return {"estado": "ok"}

    app.include_router(rutas_reportes)
    app.include_router(rutas_riesgo)
    app.include_router(rutas_atipicos)
    return app


def ejecutar() -> None:
    config = Configuracion()
    configurar_registros(json_=config.registros_json)
    servidor = uvicorn.Server(uvicorn.Config(crear_app(config), host="0.0.0.0", port=config.puerto, log_config=None))
    registro.info("ia-api en el puerto %s", config.puerto)
    ejecutar_async(servidor.serve())


if __name__ == "__main__":
    ejecutar()
