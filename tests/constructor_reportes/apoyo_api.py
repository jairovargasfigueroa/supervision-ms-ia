"""App de pruebas del constructor de reportes (503): API real (ASGI), PostgreSQL con el usuario lector, MongoDB real,
`ModeloSimulado` y un catálogo de Materiales simulado."""

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apoyo import reportes_503 as d
from apoyo.tokens import validador
from dobles.modelo_simulado import ModeloSimulado
from ia.comun.tiempo import RelojFijo
from ia.config import Configuracion
from ia.constructor_reportes.aplicacion.puertos import MaterialCatalogo, MaterialesNoDisponibles
from ia.main import crear_app

MATERIALES = {
    d.CEMENTO: MaterialCatalogo(d.CEMENTO, "CEM-IP30", "Cemento IP-30", "bolsa"),
    d.FIERRO: MaterialCatalogo(d.FIERRO, "FIE-12", "Fierro de 12 mm", "barra"),
}
DIRECTORIO = {
    "clientes": [{"id": d.C1, "nombre": "Constructora Andina SRL"}, {"id": d.C2, "nombre": "Inmobiliaria Sur"}]
}


class MaterialesSimulados:
    def __init__(self) -> None:
        self.caido = False
        self.tokens: list[str] = []

    async def materiales(self, token: str) -> Mapping[str, MaterialCatalogo]:
        self.tokens.append(token)
        if self.caido:
            raise MaterialesNoDisponibles("caído")
        return MATERIALES


@asynccontextmanager
async def cliente_api(
    motor: AsyncEngine,
    url_lector: str,
    ia_db: AsyncDatabase[dict[str, Any]],
    url_mongo: str,
    modelo: ModeloSimulado,
    materiales: MaterialesSimulados | None = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    config = Configuracion(mongo_url=url_mongo, mongo_db=ia_db.name)
    lector = create_async_engine(url_lector, pool_size=2)
    app = crear_app(
        config,
        validador=validador(),
        motor=motor,
        motor_lector=lector,
        cargar_modelo_riesgo=False,
        modelo_lenguaje=modelo,
        catalogo_materiales=materiales or MaterialesSimulados(),
    )
    app.state.reloj = RelojFijo(d.AHORA)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ia") as http,
        ):
            yield http, app
    finally:
        await lector.dispose()
