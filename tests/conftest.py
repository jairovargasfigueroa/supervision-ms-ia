"""Configuración común de las pruebas."""

import asyncio
import os
import sys
from collections.abc import Callable, Mapping

import pytest

# Ninguna prueba debe tocar las bases ni la cola locales (PostgreSQL 5433, MongoDB 27017, RabbitMQ 5672): las que
# necesitan una real usan Testcontainers y lo pasan explícito.
os.environ["IA_REPORTES_DB_URL"] = "postgresql+psycopg://nadie:nadie@127.0.0.1:1/ninguna"
os.environ["IA_MONGO_URL"] = "mongodb://127.0.0.1:1"
os.environ["IA_MONGO_DB"] = "ia_pruebas"
os.environ["IA_RABBITMQ_URL"] = "amqp://nadie:nadie@127.0.0.1:1/"


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg async no funciona con el bucle *proactor* de Windows.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"asyncio": asyncio.new_event_loop}


# Fixtures de PostgreSQL y MongoDB reales (Testcontainers, o IA_PRUEBAS_DB_URL e IA_PRUEBAS_MONGO_URL).
pytest_plugins = ["apoyo.base_datos", "apoyo.mongo"]
