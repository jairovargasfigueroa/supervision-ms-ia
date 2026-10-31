"""PostgreSQL real para las pruebas de integración: Testcontainers (o `IA_PRUEBAS_DB_URL`, una base vacía)."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

RAIZ = Path(__file__).resolve().parents[2]
TABLAS = (
    "rpt_obra_resumen",
    "rpt_planilla",
    "rpt_consumo_material",
    "rpt_movimiento",
    "rpt_cobro",
    "evento_aplicado",
    "carga_inicial",
)


def configuracion_alembic(url: str) -> Config:
    config = Config(str(RAIZ / "alembic.ini"))
    config.set_main_option("script_location", str(RAIZ / "migraciones"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configurar_registros"] = False
    return config


@pytest.fixture(scope="session")
def url_base_pruebas() -> Iterator[str]:
    url = os.environ.get("IA_PRUEBAS_DB_URL")
    if url:
        yield url
        return
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(
        "postgres:17-alpine", username="ia", password="ia", dbname="reportes_db", driver="psycopg"
    ) as contenedor:
        yield contenedor.get_connection_url()


@pytest.fixture(scope="session")
def url_migrada(url_base_pruebas: str) -> str:
    command.upgrade(configuracion_alembic(url_base_pruebas), "head")
    return url_base_pruebas


@pytest.fixture
async def motor(url_migrada: str) -> AsyncIterator[AsyncEngine]:
    """Motor sobre la base migrada, con las tablas vacías al empezar cada prueba."""
    motor = create_async_engine(url_migrada, pool_size=5)
    async with motor.begin() as conexion:
        await conexion.execute(text(f"TRUNCATE {', '.join(TABLAS)}"))
    try:
        yield motor
    finally:
        await motor.dispose()
