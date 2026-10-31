"""MongoDB real para las pruebas: Testcontainers (`mongo:8.0`) o `IA_PRUEBAS_MONGO_URL`."""

import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase


@pytest.fixture(scope="session")
def url_mongo_pruebas() -> Iterator[str]:
    url = os.environ.get("IA_PRUEBAS_MONGO_URL")
    if url:
        yield url
        return
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    contenedor = (
        DockerContainer("mongo:8.0")
        .with_exposed_ports(27017)
        .waiting_for(LogMessageWaitStrategy("Waiting for connections"))
    )
    with contenedor:
        yield f"mongodb://{contenedor.get_container_host_ip()}:{contenedor.get_exposed_port(27017)}"


@pytest.fixture
async def ia_db(url_mongo_pruebas: str) -> AsyncIterator[AsyncDatabase[dict[str, Any]]]:
    """Una base vacía por prueba (se borra al terminar)."""
    cliente: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(url_mongo_pruebas, tz_aware=True)
    nombre = f"ia_prueba_{uuid.uuid4().hex[:8]}"
    try:
        yield cliente[nombre]
    finally:
        await cliente.drop_database(nombre)
        await cliente.close()
