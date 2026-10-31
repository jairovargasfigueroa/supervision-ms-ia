# IA: estructura y librerías

Obligatorio para todo el código de este servicio. Si algo no encaja, propón el cambio antes de salirte de esta estructura.

## Librerías

Python 3.12 con `uv`. `fastapi` + `uvicorn`, `pydantic` 2 + `pydantic-settings` 2, `sqlalchemy` 2 (Core, async) + `psycopg` 3, `alembic` 1, `pymongo` 4 (`AsyncMongoClient`), `aio-pika` 10, `PyJWT[crypto]` 2 (`PyJWKClient`), `apscheduler` 3 (`AsyncIOScheduler`), `httpx` (carga inicial), `tzdata` (zonas horarias en Windows y en la imagen). Desarrollo: `pytest` 9, `pytest-asyncio` 1, `testcontainers[postgres]` 4, `ruff`, `mypy`. ML (402): `xgboost` 3, `scikit-learn` 1 (división por grupos y métricas, solo en `ml/`), `pandas` 3 (solo en `ml/`), `numpy` 2, `pyyaml` 6; grupo opcional `notebook` (`matplotlib`, `ipykernel`) para `ml/riesgo/exploracion.ipynb`. No se agregan otras librerías para lo mismo.

- SQLAlchemy se usa **sin ORM** (tablas de Core y `AsyncConnection`): las proyecciones son *upserts* (`INSERT … ON CONFLICT`) y bloqueos (`SELECT … FOR UPDATE`) explícitos.
- Montos y porcentajes con `decimal.Decimal`, nunca `float`; redondeo «mitad hacia arriba» al final (`ia.reportes.dominio.indicadores.redondear`).
- Fechas con zona (`datetime` *aware*); el «hoy» del negocio sale de un `Reloj` (`ia.comun.tiempo`) para poder fijarlo en las pruebas.

## Árbol (DDD + hexagonal ligera por módulo)

```
pyproject.toml · uv.lock · .python-version · .env.example · Dockerfile · alembic.ini
migraciones/                      Alembic de reportes_db (env.py usa IA_REPORTES_DB_URL)
  versions/0001_inicial.py        rpt_obra_resumen, rpt_planilla, rpt_consumo_material, rpt_movimiento, rpt_cobro,
                                  evento_aplicado, carga_inicial
src/ia/
  main.py                         crear_app() (FastAPI) y ejecutar() → ia-api (Uvicorn en IA_PUERTO)
  worker.py                       ejecutar() → ia-worker (consumidor + tareas programadas)
  carga_inicial.py                ejecutar() → ia-carga-inicial (código 0 si terminó bien)
  config.py                       Configuracion (pydantic-settings, prefijo IA_)
  comun/
    bucle.py                      ejecutar_async(): asyncio.run con SelectorEventLoop en Windows (psycopg async)
    errores.py                    ErrorApi (Problem Details) y códigos genéricos
    http.py                       manejadores de errores (application/problem+json), middleware X-Request-Id y encabezados
    registros.py                  configurar_registros(): JSON en producción, texto en desarrollo; request_id en cada línea
    tiempo.py                     Reloj (zona del negocio), RelojSistema, fecha_en_zona(), iso_utc()
    base_datos.py                 metadata de SQLAlchemy y crear_motor()
    mongo.py                      crear_cliente_mongo() y registro de índices (los agregan 402 y 403)
  auth/
    usuario.py                    Usuario, ROLES_ERP, roles_efectivos() (función pura según azp)
    token.py                      ValidadorToken (firma por JWKS, vencimiento, emisor, audiencia), errores
    dependencias.py               FastAPI: usuario_actual (401/503), exigir_roles(...) (403)
    visibilidad.py                ve_todas_las_obras(), puede_ver_obra(usuario, residente_id)
  mensajeria/
    sobre.py                      Sobre, leer_sobre(), MensajeInvalidoError (→ directo a fallidos)
    consumidor.py                 ConsumidorMensajes: reparte por tipo, reintentos con x-intentos, plataforma.fallidos;
                                  MensajeRecibido y CanalMensajeria (puerto de la cola; en pruebas, un doble)
    rabbit.py                     ConexionRabbit (aio-pika): topología de la cola «ia», prefetch 10, reconexión
    inbox.py                      tabla evento_aplicado y registrar_evento() (misma transacción que la proyección)
  reportes/                       (401) modelo de lectura
    dominio/
      indicadores.py              redondear, avance_pct, avance_periodo_pct, plazo_pct, estado_cobro
      valores.py                  estados, tipos y zonas (constantes del modelo de lectura)
    aplicacion/
      lecturas.py                 datos ya traducidos: ObraLeida, PlanillaAprobadaLeida, CobroLeido, MovimientoLeido…
      puertos.py                  BaseReportes, RepositorioReportes, FuenteObras, FuenteMateriales, FuenteFinanzas
      proyecciones.py             Proyecciones: un caso de uso por evento (los usa también la carga inicial)
      tareas_programadas.py       vencer_cobros(), recalcular_plazos()
      carga_inicial.py            CargaInicial (una obra por transacción, bloqueo de una sola corrida, tabla carga_inicial)
      estado.py                   EstadoReportes (GET /reportes/estado)
    infraestructura/
      tablas.py                   tablas de Core de reportes_db
      repositorio_sql.py          RepositorioReportesSql y BaseReportesSql (transacción, pg_try_advisory_lock)
      campos.py                   lectura tolerante de campos JSON (DatoInvalido); la usan eventos y clientes REST
      eventos.py                  traduce `datos` de los 6 eventos a lecturas y registra los manejadores (inbox + proyección)
      clientes_rest.py            TokenServicio (client credentials de ia-reportes), ClienteServicio (GET, 10 s,
                                  un reintento), FuenteObrasRest, FuenteMaterialesRest, FuenteFinanzasRest
      programador.py              apscheduler: 00:20 cobros vencidos, 00:30 plazo (America/La_Paz)
      web.py                      APIRouter: GET /reportes/estado (admin)
  riesgo/                         (402) riesgo de retraso
    dominio/
      variables.py                calcular_variables (misma función que el generador), niveles, factores y sus textos
    aplicacion/
      puertos.py                  ModeloRiesgo, AlmacenPredicciones, FuenteRiesgo, ContadorAtipicos, VisibilidadObras
      prediccion.py               calcular_prediccion (SIN_DATOS o probabilidad, nivel y factores)
      recalcular.py               ServicioRiesgo.recalcular_obra (pública: eventos, diario, carga inicial y 403), clave_evento
      consultas.py                ConsultasRiesgo (GET de predicciones y del modelo)
    infraestructura/
      modelo_xgboost.py           cargar_modelo (actual.txt o IA_MODELO_RIESGO_VERSION), predicción con pred_contribs
      predicciones_mongo.py       ia_db.prediccion ($setOnInsert por clave; índices registrados)
      fuente_sql.py               lectura de rpt_* para las variables, rpt_obra_resumen.riesgo_*, visibilidad, consumo_atipico
      disparadores.py             pasos posteriores de PlanillaAprobada/PlanillaObservada/ContratoRegistrado y tarea 01:00
      componer.py                 crear_servicio_riesgo (worker y carga inicial)
      web.py                      GET /predicciones, GET /obras/{obraId}/prediccion, GET /modelos/riesgo-retraso
  atipicos/                       (403) módulo hermano, misma división
ml/riesgo/                        (402) generar.py, entrenar.py, parametros.yaml, README.md (formato del CSV real),
                                  exploracion.ipynb; datos/ no se versiona
modelos/riesgo_retraso/           actual.txt y rr-X.Y.Z/ (modelo.ubj, variables.json, metricas.json, parametros.yaml)
tests/                            espejo de src/ia (y tests/ml); las de PostgreSQL o MongoDB reales llevan «integracion»
```

## Cómo se agrega un módulo hermano (402, 403)

- Carpeta `src/ia/<modulo>/{dominio,aplicacion,infraestructura}`; el dominio no importa FastAPI, SQLAlchemy, aio-pika, pymongo ni httpx.
- Rutas: un `APIRouter` en `infraestructura/web.py`, incluido en `ia.main.crear_app`.
- Eventos: el manejador de cada tipo lo registra `reportes` (`ia.reportes.infraestructura.eventos.registrar_manejadores`). Un módulo agrega pasos con `ConsumidorMensajes.agregar_previo(tipo, paso)` (corre **antes** de la transacción de PostgreSQL; idempotente por clave, por ejemplo MongoDB de la 403) y `agregar_posterior(tipo, paso)` (corre **después** de confirmarla y solo si el evento se aplicó, por ejemplo la predicción de la 402). Si un paso falla, el mensaje se reintenta como cualquier fallo.
- Tareas programadas: agregar el trabajo en `ia.worker` (mismo `AsyncIOScheduler`, zona del negocio).
- Índices de MongoDB: `ia.comun.mongo.registrar_indice(...)` en el módulo; el worker y la API los aseguran al arrancar.
