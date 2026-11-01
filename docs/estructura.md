# IA: estructura y librerías

Obligatorio para todo el código de este servicio. Si algo no encaja, propón el cambio antes de salirte de esta estructura.

## Librerías

Python 3.12 con `uv`. `fastapi` + `uvicorn`, `pydantic` 2 + `pydantic-settings` 2, `sqlalchemy` 2 (Core, async) + `psycopg` 3, `alembic` 1, `pymongo` 4 (`AsyncMongoClient`), `aio-pika` 10, `PyJWT[crypto]` 2 (`PyJWKClient`), `apscheduler` 3 (`AsyncIOScheduler`), `httpx` (carga inicial), `tzdata` (zonas horarias en Windows y en la imagen). Desarrollo: `pytest` 9, `pytest-asyncio` 1, `testcontainers[postgres]` 4, `ruff`, `mypy`. ML (402): `xgboost` 3, `scikit-learn` 1 (división por grupos y métricas, solo en `ml/`), `pandas` 3 (solo en `ml/`), `numpy` 2, `pyyaml` 6; grupo opcional `notebook` (`matplotlib`, `ipykernel`) para `ml/riesgo/exploracion.ipynb`. ML (403): `scikit-learn` 1 (Isolation Forest, también en el servicio), `numpy` 2, `joblib` 1 (`modelo.joblib`) y `pyyaml` 6. Modelo de lenguaje (503, lo reutiliza la 502): `ollama` 0.6 y `anthropic` 2, solo en `src/ia/lenguaje/infraestructura/`. No se agregan otras librerías para lo mismo.

- SQLAlchemy se usa **sin ORM** (tablas de Core y `AsyncConnection`): las proyecciones son *upserts* (`INSERT … ON CONFLICT`) y bloqueos (`SELECT … FOR UPDATE`) explícitos.
- Montos y porcentajes con `decimal.Decimal`, nunca `float`; redondeo «mitad hacia arriba» al final (`ia.reportes.dominio.indicadores.redondear`).
- Fechas con zona (`datetime` *aware*); el «hoy» del negocio sale de un `Reloj` (`ia.comun.tiempo`) para poder fijarlo en las pruebas.

## Árbol (DDD + hexagonal ligera por módulo)

```
pyproject.toml · uv.lock · .python-version · .env.example · Dockerfile · alembic.ini
migraciones/                      Alembic de reportes_db (env.py usa IA_REPORTES_DB_URL)
  versions/0001_inicial.py        rpt_obra_resumen, rpt_planilla, rpt_consumo_material, rpt_movimiento, rpt_cobro,
                                  evento_aplicado, carga_inicial
  versions/0002_lector_reportes.py GRANT SELECT de ia_reportes_lector sobre las 5 tablas rpt_* (503)
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
  atipicos/                       (403) consumos de material atípicos (Isolation Forest)
    dominio/
      variables.py                SalidaMaterial, Historial, calcular_variables_salida() (13 variables, con salidas_1h y salidas_3h)
      historial.py                HistorialEnMemoria, recorrer_en_orden() (generador y reentrenamiento)
      motivos.py                  Percentiles, motivos_consumo() (1 a 3 motivos)
      revision.py                 reglas del comentario y de los estados de la revisión
      consumo.py · valores.py     ConsumoAtipico; estados, límites
    aplicacion/
      puertos.py                  ModeloAtipicos, FuenteHistorial, EventosAplicados, RepositorioAtipicos, ObrasReportes,
                                  NotificadorRiesgo (recálculo de la 402)
      deteccion.py                DetectarConsumoAtipico (paso previo de MovimientoRegistrado)
      consultas.py · revisar.py   lista, resumen por obra y revisión; errores.py
    infraestructura/
      modelo.py                   carga de modelos/consumos_atipicos/<version>/ (joblib + percentiles.json)
      salidas_sql.py              historial desde rpt_movimiento, inbox, salidas para reentrenar
      obras_sql.py                visibilidad desde rpt_obra_resumen
      repositorio_mongo.py        ia_db.consumo_atipico y su índice
      eventos.py                  registrar_deteccion(): agrega el paso previo y el posterior del recálculo
      riesgo.py                   RecalculoRiesgo402: NotificadorRiesgo con ServicioRiesgo.recalcular_obra
      web.py                      APIRouter: GET /consumos-atipicos, GET /consumos-atipicos/resumen, PUT …/revision
  lenguaje/                       (503; lo usa la 502) puerto del modelo de lenguaje
    dominio/mensajes.py           Mensaje (SISTEMA, USUARIO, ASISTENTE, HERRAMIENTA), DefinicionHerramienta,
                                  LlamadaHerramienta (id, nombre, argumentos), RespuestaModelo (texto, llamadas), Opciones
    aplicacion/puerto.py          ModeloLenguaje (Protocol) y errores LlmNoDisponible, LlmTiempoAgotado, SalidaNoValida
    infraestructura/ollama.py     ModeloOllama (cliente `ollama`, AsyncClient)
    infraestructura/anthropic.py  ModeloAnthropic (SDK `anthropic`, AsyncAnthropic)
    infraestructura/esquemas.py   adaptar_esquema(esquema, proveedor): JSON Schema que acepta cada proveedor
    infraestructura/componer.py   crear_modelo_lenguaje(config) según IA_LLM_PROVEEDOR
  constructor_reportes/           (503) catálogo → receta → validación → consulta parametrizada (REST y MCP)
    dominio/catalogo.py           conjuntos: columnas, filtros, dimensiones, medidas, valores por defecto, ejemplos
    dominio/receta.py             RecetaReporte (Pydantic, extra="forbid") y su JSON Schema
    dominio/validacion.py         validar_receta(receta, conjunto, modo) → RecetaValidada o lista de errores
    dominio/atajos.py             atajos: receta fija en función de «hoy»
    dominio/resultado.py          Columna, Resultado (filas como texto), totales para el resumen, plantilla
    aplicacion/puertos.py         EjecutorConsultas, CatalogoMateriales, RepositorioConsultas, LimitadorConsultas
    aplicacion/motor.py           MotorReportes.ejecutar(receta_validada, modo, directorio) → Resultado (REST y MCP)
    aplicacion/interpretar.py     InterpretarPedido: texto → RecetaReporte (LLM)
    aplicacion/consultar.py       ConsultarReporte: caso de uso de las personas (límite, interpretar, motor, resumen, guardar)
    infraestructura/expresiones.py     expresión de SQLAlchemy Core de cada columna, filtro, dimensión y medida (constantes)
    infraestructura/constructor_sql.py arma el select() de Core desde la receta validada (sin texto del usuario ni del modelo)
    infraestructura/ejecutor_sql.py    ejecuta con el usuario lector, READ ONLY y statement_timeout de 5 s
    infraestructura/atipicos_mongo.py  consulta de ia_db.consumo_atipico (filtro y agregación armados con constantes)
    infraestructura/materiales_rest.py GET /materiales de Materiales con el token del usuario (caché de 5 min)
    infraestructura/consultas_mongo.py ia_db.consulta_reporte
    infraestructura/limite_mongo.py    ia_db.limite_consulta
    infraestructura/web.py             POST /consultas-reporte, GET /consultas-reporte, GET /catalogo-reportes
  seguridad/                      (504; la usan 502 y 503) como_dato (delimitar.py), texto_seguro (limpiar.py),
                                  reglas comunes del mensaje de sistema (reglas_sistema.py)
ml/riesgo/                        (402) generar.py, entrenar.py, parametros.yaml, README.md (formato del CSV real),
                                  exploracion.ipynb; datos/ no se versiona
modelos/riesgo_retraso/           actual.txt y rr-X.Y.Z/ (modelo.ubj, variables.json, metricas.json, parametros.yaml)
ml/atipicos/                      (403) generar.py + parametros.yaml (datos sintéticos), entrenar.py; datos/ no se versiona
modelos/consumos_atipicos/        versiones del modelo (modelo.joblib, percentiles.json, metricas.json, parametros.yaml)
                                  y actual.txt
tests/                            espejo de src/ia (y tests/ml); las de PostgreSQL o MongoDB reales llevan «integracion»
```

## Cómo se agrega un módulo hermano (402, 403)

- Carpeta `src/ia/<modulo>/{dominio,aplicacion,infraestructura}`; el dominio no importa FastAPI, SQLAlchemy, aio-pika, pymongo ni httpx.
- Rutas: un `APIRouter` en `infraestructura/web.py`, incluido en `ia.main.crear_app`.
- Eventos: el manejador de cada tipo lo registra `reportes` (`ia.reportes.infraestructura.eventos.registrar_manejadores`). Un módulo agrega pasos con `ConsumidorMensajes.agregar_previo(tipo, paso)` (corre **antes** de la transacción de PostgreSQL; idempotente por clave, por ejemplo MongoDB de la 403) y `agregar_posterior(tipo, paso)` (corre **después** de confirmarla y solo si el evento se aplicó, por ejemplo la predicción de la 402). Si un paso falla, el mensaje se reintenta como cualquier fallo.
- Tareas programadas: agregar el trabajo en `ia.worker` (mismo `AsyncIOScheduler`, zona del negocio).
- Índices de MongoDB: `ia.comun.mongo.registrar_indice(...)` en el módulo (con TTL o filtro parcial si hace falta); el worker y la API los aseguran al arrancar.

## Modelo de lenguaje (capa 5)

- Solo `lenguaje/infraestructura/` importa `ollama` o `anthropic`; los casos de uso usan el puerto `ModeloLenguaje`. `reportes`, `riesgo` y `atipicos` (capa 4) no usan modelos de lenguaje.
- La salida de `generar_json` siempre se vuelve a validar con Pydantic en el código que la pide.
- Las pruebas nunca llaman a un modelo real: `ModeloSimulado` (`tests/dobles/`) y, para los adaptadores, un transporte HTTP simulado de `httpx`.
