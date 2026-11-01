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
os.environ["IA_REPORTES_DB_URL_LECTURA"] = "postgresql+psycopg://nadie:nadie@127.0.0.1:1/ninguna"
os.environ["IA_MATERIALES_URL"] = "http://127.0.0.1:1"
os.environ["IA_OBRAS_URL"] = "http://127.0.0.1:1"
os.environ["IA_FINANZAS_URL"] = "http://127.0.0.1:1"
# Nunca un modelo de lenguaje real (503, 502): Ollama a una dirección inexistente y sin clave de Anthropic.
os.environ["IA_LLM_PROVEEDOR"] = "ollama"
os.environ["IA_LLM_MODELO"] = ""
os.environ["IA_OLLAMA_URL"] = "http://127.0.0.1:1"
os.environ["IA_ANTHROPIC_API_KEY"] = ""
# Cliente MCP y servidor MCP (501): los valores por defecto, aunque el .env local tenga otros (el secreto real nunca).
os.environ["IA_AGENTES_CLIENT_ID"] = "ia-agentes"
os.environ["IA_AGENTES_CLIENT_SECRET"] = ""
os.environ["IA_OBRAS_MCP_URL"] = "http://localhost:8081/mcp"
os.environ["IA_MATERIALES_MCP_URL"] = "http://localhost:8082/mcp"
os.environ["IA_FINANZAS_MCP_URL"] = "http://localhost:8083/mcp"
os.environ["IA_PROPIO_MCP_URL"] = "http://localhost:8085/mcp"
os.environ["IA_MCP_HOSTS_PERMITIDOS"] = "localhost:8085,127.0.0.1:8085"
# Agentes (502): la programación diaria con sus valores por defecto.
os.environ["IA_AGENTES_CRON"] = "0 6 * * *"
os.environ["IA_AGENTES_PROGRAMADOS_ACTIVOS"] = "true"


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
    # psycopg async no funciona con el bucle *proactor* de Windows.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return {"asyncio": asyncio.new_event_loop}


# Fixtures de PostgreSQL y MongoDB reales (Testcontainers, o IA_PRUEBAS_DB_URL e IA_PRUEBAS_MONGO_URL).
pytest_plugins = ["apoyo.base_datos", "apoyo.mongo"]
