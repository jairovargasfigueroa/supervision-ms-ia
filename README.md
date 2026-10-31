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

uv run pytest -q                     # pruebas (las de integración levantan PostgreSQL y MongoDB con Testcontainers)
uv run ruff check . ; uv run ruff format --check . ; uv run mypy
```

Las pruebas de integración necesitan Docker (Testcontainers con `postgres:17-alpine` y `mongo:8.0`). Para usar otra base vacía en su lugar: `IA_PRUEBAS_DB_URL=postgresql+psycopg://usuario:clave@host:puerto/base_vacia` e `IA_PRUEBAS_MONGO_URL=mongodb://usuario:clave@host:puerto` (usa la base `ia_db_pruebas` y la borra en cada prueba).

## Modelo de consumos atípicos (403)

Isolation Forest de scikit-learn, solo con CPU. Los artefactos de cada versión van en `modelos/consumos_atipicos/<version>/` (`modelo.joblib`, `percentiles.json`, `metricas.json`, `parametros.yaml`) y `modelos/consumos_atipicos/actual.txt` dice cuál carga el worker (o `IA_MODELO_ATIPICOS_VERSION`). Sin modelo, el worker registra el error y sigue sin detectar.

```powershell
uv run python -m ml.atipicos.generar --semilla 2026                          # ml/atipicos/datos/salidas_sinteticas.csv (no se versiona)
uv run python -m ml.atipicos.entrenar --fuente sintetica --version ca-1.0.0
uv run python -m ml.atipicos.entrenar --fuente real --version ca-1.1.0       # requiere ≥ 500 salidas en rpt_movimiento
```

`entrenar` termina con código 1 y no cambia `actual.txt` si no se cumplen las métricas mínimas (diseño de la 403). Parámetros del generador: `ml/atipicos/parametros.yaml`.

Tiempo medido el 31/10/2026 en la computadora del equipo (Intel Core i7-11370H, 8 hilos, sin GPU), con la semilla 2026: generar las 48 849 salidas tarda 5,2 s y entrenar (leer el CSV, ajustar y medir) 3,6 s; los dos comandos completos, con el arranque de Python y la importación de scikit-learn, 14 s (objetivo: menos de un minuto).

Métricas de `ca-1.0.0` sobre el 20 % de prueba (semilla 2026): sensibilidad 0,53 (mínimo 0,70), precisión 0,53, AUC-ROC 0,94 y 2,3 % marcadas. **No cumple la sensibilidad mínima**, así que `ca-1.0.0` todavía no está versionado (ver la nota de la 403 en `docs/specs/403-consumos-atipicos/tareas.md`).

## Docker

Una imagen, tres comandos (`ia-api` por defecto, `ia-worker`, `ia-carga-inicial`):

```powershell
docker build -t supervision-ia .
docker run --env-file .env -p 8085:8085 supervision-ia
docker run --env-file .env supervision-ia ia-carga-inicial
```
