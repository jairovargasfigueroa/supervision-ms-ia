# supervision-ms-ia

Servicio de IA de la Plataforma de supervisión de obras: modelo de lectura para reportes (CQRS), predicciones y detección de anomalías (capa 4), y agentes y reportes generativos (capa 5).

Python 3.12 · FastAPI · PostgreSQL (`reportes_db`) · MongoDB (`ia_db`) · RabbitMQ (`aio-pika`). Detalle en `CLAUDE.md` y `docs/`.

## Preparar la máquina

El proyecto usa [`uv`](https://docs.astral.sh/uv/) para Python y las dependencias (`pyproject.toml` + `uv.lock`).

```powershell
# 1. Instalar uv (con cualquier Python que ya tengas; en la máquina de Jairo, el 3.12 del lanzador «py»)
py -3.12 -m pip install --user uv
#    queda en %APPDATA%\Python\Python312\Scripts\uv.exe: agrega esa carpeta al PATH

# 2. Instalar el Python 3.12 que gestiona uv (el 3.12.0rc2 instalado en Windows no sirve: es una versión preliminar)
uv python install 3.12
uv python find 3.12        # debe mostrar ...\uv\python\cpython-3.12.x...\python.exe

# 3. Crear el entorno e instalar las dependencias (también las de desarrollo)
uv sync
```

Si `uv python install` termina con «Missing expected target directory for Python minor version link», el Python igual quedó instalado: borra la carpeta `%APPDATA%\uv\python\cpython-3.12-windows-x86_64-none` (el enlace roto) y `uv python find 3.12` lo encuentra.

## Configuración

Copia `.env.example` a `.env` y completa los valores vacíos (la clave de RabbitMQ y el secreto de `ia-reportes` salen de `infra/.env`). El `.env` nunca se sube.

## Comandos

```powershell
uv run alembic upgrade head          # crea o actualiza las tablas de reportes_db
uv run ia-api                        # API en http://localhost:8085 (GET /salud)
uv run ia-worker                     # consumidor de la cola «ia» y tareas programadas
uv run ia-carga-inicial              # carga inicial (una vez, con Obras, Materiales y Finanzas levantados)

uv run pytest -q                     # pruebas (las de integración levantan PostgreSQL con Testcontainers)
uv run ruff check . ; uv run ruff format --check . ; uv run mypy
```

Las pruebas de integración necesitan Docker (Testcontainers con `postgres:17-alpine`). Para usar otra base vacía en su lugar: `IA_PRUEBAS_DB_URL=postgresql+psycopg://usuario:clave@host:puerto/base_vacia`.

## Docker

Una imagen, tres comandos (`ia-api` por defecto, `ia-worker`, `ia-carga-inicial`):

```powershell
docker build -t supervision-ia .
docker run --env-file .env -p 8085:8085 supervision-ia
docker run --env-file .env supervision-ia ia-carga-inicial
```
