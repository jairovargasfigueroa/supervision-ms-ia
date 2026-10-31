"""Configuración común de las pruebas."""

import asyncio
import sys
from collections.abc import Callable, Mapping

import pytest


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg async no funciona con el bucle *proactor* de Windows.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"asyncio": asyncio.new_event_loop}


# Fixtures de PostgreSQL y MongoDB reales (Testcontainers, o IA_PRUEBAS_DB_URL e IA_PRUEBAS_MONGO_URL).
pytest_plugins = ["apoyo.base_datos", "apoyo.mongo"]
