# Una sola imagen para los tres procesos del servicio de IA:
#   ia-api            FastAPI en el puerto 8085 (comando por defecto)
#   ia-worker         consumidor de la cola «ia» y tareas programadas
#   ia-carga-inicial  carga inicial de la base de reportes (corre una vez y termina)
# En Docker Compose se elige con «command:». Las migraciones: «alembic upgrade head».

FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    IA_ENTORNO=produccion

WORKDIR /app

# Primero solo las dependencias, para aprovechar la caché de capas.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY README.md alembic.ini ./
COPY migraciones ./migraciones
COPY src ./src
# Modelos versionados en el repositorio (403): el servicio solo carga estos archivos.
COPY modelos ./modelos
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

RUN useradd --system --uid 10001 ia
USER ia

EXPOSE 8085
CMD ["ia-api"]
