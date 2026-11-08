# Imagen de producción del servicio de IA (601): una sola imagen para sus tres procesos.
#   ia-api            aplica «alembic upgrade head» y atiende FastAPI en IA_PUERTO (8085). Comando por defecto.
#   ia-worker         consumidor de la cola «ia», tareas programadas y host de los agentes
#   ia-carga-inicial  carga inicial de la base de reportes (corre una vez y termina)
# Se elige con el comando: «docker run <imagen> ia-worker» o «command:» en Compose. Ninguna dirección ni secreto viaja
# en la imagen: todo entra por las variables IA_* al arrancar (.env.example).

# --- construir: dependencias y paquete con uv (uv no queda en la imagen final) ---
FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.12-slim AS construir

COPY --from=uv /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Primero solo las dependencias (capa que cambia poco). nvidia-nccl-cu13 (XGBoost en Linux) solo sirve para entrenar
# con varias GPU: la predicción en CPU no la usa.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-install-package nvidia-nccl-cu13

# El paquete «ia» se instala dentro del entorno (sin editable): la imagen final no necesita src/.
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --no-install-package nvidia-nccl-cu13

# --- final: solo el entorno, las migraciones y los modelos ---
FROM python:3.12-slim AS final

LABEL org.opencontainers.image.source="https://github.com/jairovargasfigueroa/supervision-ms-ia" \
      org.opencontainers.image.description="Servicio de IA de la plataforma de supervisión de obras (ia-api, ia-worker, ia-carga-inicial)"

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    IA_ENTORNO=produccion \
    IA_PUERTO=8085

RUN groupadd --system --gid 10001 ia \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/ia --shell /usr/sbin/nologin ia

WORKDIR /app

COPY --from=construir /app/.venv /app/.venv
COPY alembic.ini ./
COPY migraciones ./migraciones
# Modelos versionados en el repositorio (402 y 403): el servicio solo carga estos archivos.
COPY modelos ./modelos
COPY --chmod=0755 docker/entrada.sh /usr/local/bin/ia-entrada
COPY docker/salud.py /usr/local/lib/ia/salud.py

USER 10001:10001

EXPOSE 8085

# ia-api: GET /salud; ia-worker e ia-carga-inicial: basta con que el proceso esté vivo (docker/salud.py).
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD ["python", "/usr/local/lib/ia/salud.py"]

ENTRYPOINT ["ia-entrada"]
CMD ["ia-api"]
