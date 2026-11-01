# Servicio de IA

Servicio de la capa 4 (y luego 3 y 5): arma el **modelo de lectura** de los reportes que cruzan servicios (`reportes_db`, tablas `rpt_*`) con los eventos de RabbitMQ y una carga inicial por REST (401); sobre él calcula el riesgo de retraso (402) y los consumos atípicos (403). En la capa 5 suma agentes y reportes por voz.

Parte de la «Plataforma de supervisión de obras». Si existe `../../CLAUDE.md` (carpeta que agrupa todos los proyectos), léelo primero: tiene las reglas transversales, el plan y los contratos generales.

**Estado:** 401 (base de reportes), 402 (riesgo de retraso con XGBoost; `ml/riesgo/` y `modelos/riesgo_retraso/`), 403 (consumos atípicos con Isolation Forest; `ml/atipicos/` y `modelos/consumos_atipicos/`; un atípico recalcula el riesgo de la obra) y 503 (constructor de reportes: `lenguaje/` y `constructor_reportes/`). No implementes una funcionalidad sin su especificación aprobada (`docs/specs/` de la carpeta general y `docs/specs/NNN-nombre/tareas.md` de aquí).

## Stack

- Python 3.12 con `uv` (`pyproject.toml` + `uv.lock`) · paquete `src/ia`
- FastAPI + Uvicorn (puerto `8085`, `IA_PUERTO`) · `pydantic` 2 + `pydantic-settings` (variables `IA_*`)
- PostgreSQL `reportes_db` con SQLAlchemy 2 (Core, async) + `psycopg` 3; migraciones con Alembic (`migraciones/`)
- MongoDB `ia_db` con `pymongo` (`AsyncMongoClient`; lo llenan 402, 403 y la capa 5)
- Modelo de lenguaje (503): `ollama` 0.6 y `anthropic` 2 detrás del puerto `ModeloLenguaje`
- RabbitMQ con `aio-pika` · token con `PyJWT` (`PyJWKClient`) · tareas programadas con `apscheduler` 3 · `httpx` (carga inicial)
- Pruebas con `pytest` + `pytest-asyncio`; PostgreSQL real con Testcontainers · lint y formato con `ruff` · tipos con `mypy --strict`

## Comandos

```sh
uv sync                      # entorno y dependencias (ver README para instalar uv y Python 3.12)
uv run alembic upgrade head  # migraciones de reportes_db
uv run ia-api                # API (GET /salud)
uv run ia-worker             # consumidor de la cola «ia» + tareas programadas
uv run ia-carga-inicial      # carga inicial (una vez; código 0 si terminó bien)
uv run pytest -q             # pruebas (Docker para Testcontainers)
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Configuración local: copiar `.env.example` a `.env` y completar. **`.env` nunca se sube.** En Windows, psycopg async necesita el bucle *selector*: los tres comandos lo eligen solos (`ia.comun.bucle`).

## Arquitectura: CQRS + DDD + hexagonal ligera por módulo

- **CQRS solo para lo que cruza servicios:** cada servicio sigue escribiendo y leyendo lo suyo; aquí vive un modelo de lectura desnormalizado alimentado por **proyecciones** de eventos. Sin *event sourcing*: el modelo se puede rearmar con la carga inicial.
- Un módulo por contexto (`reportes` en la 401; `riesgo` en la 402, `atipicos` en la 403, `lenguaje` y `constructor_reportes` en la 503 como módulos hermanos), cada uno con `dominio/` (reglas puras: sin FastAPI, SQLAlchemy, aio-pika ni httpx), `aplicacion/` (casos de uso y puertos) e `infraestructura/` (SQL, eventos, REST, web).
- Puertos solo para lo externo que cambia: la base (`BaseReportes`, `RepositorioReportes`) y los clientes REST de la carga inicial (`FuenteObras`, `FuenteMateriales`, `FuenteFinanzas`). La cola se abstrae en `mensajeria` (`CanalMensajeria`).
- Las proyecciones son casos de uso compartidos por los dos adaptadores de entrada (consumidor de eventos y carga inicial): las reglas son las mismas por los dos caminos.

Estructura exacta y librerías permitidas: `docs/estructura.md`.

## Reglas del proyecto

- Nadie más escribe en `reportes_db`, y este servicio no lee las bases de otros: escucha eventos y, solo en la carga inicial, hace `GET` a endpoints que ya existen con el token de `ia-reportes`.
- Cada evento se aplica en **una transacción** que inserta `evento_aplicado` (inbox) y actualiza las `rpt_*`; cada escritura es idempotente por fila (`INSERT … ON CONFLICT`).
- `PlanillaAprobada`, `PlanillaObservada` y la carga de cada obra bloquean antes la fila de la obra (`SELECT … FOR UPDATE`); el avance se recalcula desde `rpt_planilla`, nunca se suma.
- El secreto de `ia-reportes` solo lo lee el comando de carga inicial (nunca `ia-api` ni `ia-worker`).
- La capa 4 (`reportes`, `riesgo`, `atipicos`) no usa modelos de lenguaje. Desde la capa 5, el modelo de lenguaje (Ollama o Anthropic, `IA_LLM_PROVEEDOR`) se usa solo por el puerto `ModeloLenguaje` (`src/ia/lenguaje/`) y nunca escribe SQL: el constructor de reportes (503) valida su receta contra el catálogo y arma la consulta con SQLAlchemy Core y valores enlazados, con el usuario de solo lectura `ia_reportes_lector`.
- `IA_ANTHROPIC_API_KEY` es un secreto: solo en `.env` o en el gestor de secretos (vacía en `.env.example`).

## Documentación

- `docs/estructura.md`: **librerías permitidas y estructura de carpetas y archivos. Obligatorio leerlo antes de escribir código.**
- `docs/servicio.md`: responsabilidades, procesos y qué no hace.
- `docs/datos.md`: tablas de `reportes_db` y colecciones de `ia_db`.
- `docs/contratos.md`: lo que expone (REST), escucha (eventos) y consume (REST de la carga inicial).
- `docs/specs/`: tareas por funcionalidad (requisitos y diseño en la carpeta general).

## Commits

Conventional Commits en español e imperativo (`feat(reportes): agrega la proyección de PlanillaAprobada`). Sin coautoría ni firmas de la IA.

Git Flow: ramas `feat/`, `fix/`, `docs/`… desde `develop`, que entran por *pull request*; nunca hagas *push* a `main` ni a `develop` (están protegidas). Detalle y definición de terminado: `docs/flujo-trabajo.md` del repo `supervision-docs`.
