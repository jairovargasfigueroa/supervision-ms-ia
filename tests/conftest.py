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


# Fixtures de PostgreSQL real (Testcontainers o IA_PRUEBAS_DB_URL).
pytest_plugins = ["apoyo.base_datos"]
