"""Entorno de Alembic: conexión síncrona con psycopg 3 a `reportes_db`."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config

if config.config_file_name is not None and config.attributes.get("configurar_registros", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _url() -> str:
    # Las pruebas pasan la URL en la configuración; si no, IA_REPORTES_DB_URL (o el .env).
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from ia.config import Configuracion

    return Configuracion().reportes_db_url


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    motor = create_engine(_url(), poolclass=pool.NullPool)
    with motor.connect() as conexion:
        context.configure(connection=conexion)
        with context.begin_transaction():
            context.run_migrations()
    motor.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
