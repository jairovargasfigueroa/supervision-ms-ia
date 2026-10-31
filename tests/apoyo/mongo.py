"""MongoDB real para las pruebas de integración: Testcontainers con `mongo:8.0` (o `IA_PRUEBAS_MONGO_URL`)."""

import os
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from ia.comun.mongo import asegurar_indices, crear_cliente_mongo

BASE_PRUEBAS = "ia_db_pruebas"


@pytest.fixture(scope="session")
def url_mongo_pruebas() -> Iterator[str]:
    url = os.environ.get("IA_PRUEBAS_MONGO_URL")
    if url:
        yield url
        return
    from testcontainers.community.mongodb import MongoDbContainer

    with MongoDbContainer("mongo:8.0") as contenedor:
        url = contenedor.get_connection_url()
        _esperar(url)
        yield url


def _esperar(url: str, segundos: float = 30) -> None:
    """La imagen arranca un servidor temporal para crear el usuario y luego se reinicia: se espera al definitivo."""
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    limite = time.monotonic() + segundos
    while True:
        cliente: MongoClient[dict[str, Any]] = MongoClient(url, serverSelectionTimeoutMS=2000)
        try:
            cliente.admin.command("ping")
            return
        except PyMongoError:
            if time.monotonic() > limite:
                raise
            time.sleep(0.5)
        finally:
            cliente.close()


@pytest.fixture
async def ia_db(url_mongo_pruebas: str) -> AsyncIterator[AsyncDatabase[dict[str, Any]]]:
    """`ia_db` vacía al empezar cada prueba, con los índices registrados."""
    cliente = crear_cliente_mongo(url_mongo_pruebas)
    await cliente.drop_database(BASE_PRUEBAS)
    base = cliente[BASE_PRUEBAS]
    await asegurar_indices(base)
    try:
        yield base
    finally:
        await cliente.close()
