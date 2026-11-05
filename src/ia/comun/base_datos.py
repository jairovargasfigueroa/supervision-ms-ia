"""Conexión a `reportes_db` (PostgreSQL) con SQLAlchemy Core async y `psycopg` 3."""

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ia.comun.tls import argumentos_postgres

# Una sola metadata para las tablas de todos los módulos (las crea Alembic, no `create_all`).
metadata = MetaData()


def crear_motor(url: str, tamano_pool: int = 10) -> AsyncEngine:
    """Motor async. `pool_pre_ping` evita conexiones muertas después de reiniciar PostgreSQL. TLS por `sslmode` en la
    URL; con `IA_CA_EXTRA`, `sslrootcert` (601)."""
    return create_async_engine(
        url, pool_size=tamano_pool, max_overflow=5, pool_pre_ping=True, connect_args=argumentos_postgres(url)
    )
