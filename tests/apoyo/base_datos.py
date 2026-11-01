"""PostgreSQL real para las pruebas de integración: Testcontainers (o `IA_PRUEBAS_DB_URL`, una base vacía)."""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
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


# Usuario de solo lectura del constructor de reportes (503). En desarrollo lo crea el script de inicio de PostgreSQL
# de infra; en las pruebas, esta fixture, igual que ese script, antes de migrar (la 0002 le da los GRANT).
ROL_LECTOR = "ia_reportes_lector"
CLAVE_LECTOR = "lector-pruebas"


def _crear_rol_lector(url: str) -> None:
    motor = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with motor.connect() as conexion:
            existe = conexion.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": ROL_LECTOR}).scalar()
            if not existe:
                conexion.execute(text(f"CREATE ROLE {ROL_LECTOR} LOGIN PASSWORD '{CLAVE_LECTOR}'"))
            conexion.execute(text(f"ALTER ROLE {ROL_LECTOR} SET default_transaction_read_only = on"))
    finally:
        motor.dispose()


@pytest.fixture(scope="session")
def url_migrada(url_base_pruebas: str) -> str:
    _crear_rol_lector(url_base_pruebas)
    command.upgrade(configuracion_alembic(url_base_pruebas), "head")
    return url_base_pruebas


@pytest.fixture(scope="session")
def url_lector(url_migrada: str) -> str:
    """La misma base con el usuario `ia_reportes_lector` (IA_REPORTES_DB_URL_LECTURA)."""
    return make_url(url_migrada).set(username=ROL_LECTOR, password=CLAVE_LECTOR).render_as_string(hide_password=False)


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
