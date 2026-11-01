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

Variables: las 11 del diseño más `salidas_1h` y `salidas_3h` (salidas del mismo material en la obra en la última hora y en las últimas 3 horas, incluida esta), y `IsolationForest(n_estimators=200, contamination=0.02, max_samples=4096, random_state=2026)`. Decisión de Jairo (31/10/2026): con solo las variables del diseño y `max_samples` de 256 (el de scikit-learn) la sensibilidad era 0,53, porque las ráfagas no se distinguían de un día normal con varias salidas y las salidas de noche o en domingo casi nunca entraban en la muestra de 256 de cada árbol. Motivo nuevo: «Hubo X salidas de este material en la última hora».

Métricas de `ca-1.0.0` sobre el 20 % de prueba (semilla 2026): sensibilidad 0,72 (mínimo 0,70), precisión 0,80 (mínimo 0,50), AUC-ROC 0,97 (mínimo 0,85) y 2,1 % marcadas (de 1 % a 3 %). Sensibilidad por tipo: CANTIDAD 0,81, HORARIO 0,98, RAFAGA 0,41 (la primera salida de una ráfaga no se distingue de una normal) y STOCK 0,81. Con otras semillas del generador (1 a 4) la sensibilidad va de 0,65 a 0,75: está cerca del mínimo.

Tiempo medido el 31/10/2026 en la computadora del equipo (Intel Core i7-11370H, 8 hilos, sin GPU), con la semilla 2026: generar las 48 849 salidas tarda 4,8 s y entrenar (leer el CSV, ajustar, medir y guardar) 1,6 s; los dos comandos completos, con el arranque de Python y la importación de scikit-learn, 15 s (objetivo: menos de un minuto).

## Docker

Una imagen, tres comandos (`ia-api` por defecto, `ia-worker`, `ia-carga-inicial`):

```powershell
docker build -t supervision-ia .
docker run --env-file .env -p 8085:8085 supervision-ia
docker run --env-file .env supervision-ia ia-carga-inicial
```

## Prueba manual con el modelo real (capa 5)

Prueba manual completa pendiente (decisión de Jairo, 01/11/2026); con qwen3:8b la interpretación queda cerca del límite de 20 s.

Mediciones parciales del 01/11/2026 con Ollama (`qwen3:8b` Q4_K_M, contexto 16384, RTX 3060 Laptop de 6 GB: el modelo queda 64 % en la GPU), datos de prueba recién cargados (D = 2026-11-01). Anthropic sin probar (falta la clave).

**Calentar el modelo antes de usarlo.** La primera carga tardó de 135 a 166 s (las siguientes, de 49 a 67 s). Si la primera consulta llega con el modelo sin cargar, se corta a los 20 s y Ollama no termina de cargarlo. Antes de probar:

```sh
curl -s http://127.0.0.1:11434/api/chat -d '{"model":"qwen3:8b","messages":[{"role":"user","content":"ok"}],"think":false,"stream":false,"keep_alive":"30m","options":{"num_ctx":16384}}'
```

**Reportes por voz (503).**

- Interpretación (unas 2600 fichas de entrada): de 1,7 a 26,8 s. De las 15 preguntas del dominio, 5 se cortaron a los 20 s en el servicio. Sin límite (solo para medir): media de 15,7 s, máximo de 26,8 s y 5 de 15 por encima de 20 s; el tiempo lo marca la salida (unas 6 fichas por segundo).
- Recetas: **2 de 15 válidas** («Promedio de avance por zona», «¿Cuántos consumos atípicos hay por obra?»), también sin límite de tiempo. Errores repetidos del modelo (`422 RECETA_INVALIDA`): métricas sin `agruparPor` (6), `EN` con un valor que no es lista (6), `columnas` junto con `agruparPor` (4), `CONTEO` con campo (2), filtros que no existen (2), orden por una columna fuera de la salida (1). `validar_receta` las rechazó todas bien.
- Las 5 preguntas que no corresponden a ningún conjunto: las 5 con `422 INTENCION_NO_RECONOCIDA`, en 1,7 a 5,4 s.
- Atajos: **10 de 10** respondieron con las cifras esperadas de los datos de prueba, en 4,5 a 23 s, con resumen del modelo en los 10. Un resumen con una cifra mal (`COBROS_POR_VENCER`: «promedio de 28 días», es 14) y tres confusos (`AVANCE_OBRAS` suma la columna «Diferencia», `SALIDAS_SIN_STOCK` mezcla el conteo con la cantidad y `CONSUMOS_ATIPICOS_PENDIENTES` suma el puntaje).
- Experimento con contexto de 8192 (sin cambiar el servicio): el modelo queda un 70 % en la GPU; interpretación con media de 12,7 s, máximo de 20,4 s y 1 de 15 por encima de 20 s, pero con las mismas **2 de 15 recetas válidas**.

**Agentes (502).** Una ejecución de cada agente, sin pasar nunca de 8 llamadas ni de 3 minutos. Cada vuelta de `conversar` tardó de 10 a 46 s (de 2300 a 3400 fichas de entrada).

| Agente | Resultado | Pasos | Tiempo |
|---|---|---|---|
| Seguimiento de obras | `FALLIDA` (`LLM_NO_DISPONIBLE`): la segunda vuelta y el reporte se cortaron a los 60 s | 3 iniciales y 3 `buscar_diario`, sin errores | 2 min 51 s |
| Control de materiales | `TERMINADA`, sin hallazgos ni sugerencias («No se encontraron hallazgos…»), aunque había `CEM-IP30` `BAJO` y un consumo atípico pendiente | 3 iniciales y 3 `consumos_partida` | 1 min 58 s |
| Control de cobros | `FALLIDA` (`LLM_NO_DISPONIBLE`): el reporte se cortó a los 60 s | 2 iniciales y un `consultar_reporte` que el host rechazó (`ARGUMENTO_INVALIDO`) sin llamar al servidor | 1 min 41 s |

**Entradas maliciosas (504).** No se corrieron contra el modelo real; están cubiertas con el modelo simulado en `tests/seguridad/`.

