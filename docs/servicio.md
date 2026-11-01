# Servicio de IA: responsabilidades

## Qué hace (401)

**Mecanismo: CQRS solo para lo que cruza servicios.** Obras, Materiales y Finanzas siguen escribiendo y leyendo lo suyo. Este servicio arma un **modelo de lectura** desnormalizado (`reportes_db`, tablas `rpt_*`) con proyecciones de los eventos de RabbitMQ, para que las predicciones (402, 403) y los reportes por voz (capa 5) no consulten las bases de los demás. No hay *event sourcing*: el modelo se puede volver a armar con la carga inicial. Consistencia eventual: va unos segundos detrás de los servicios, y el plazo y el vencimiento de cobros se recalculan una vez al día.

| Proceso | Comando | Qué hace |
|---|---|---|
| `ia-api` | `uv run ia-api` | FastAPI en el puerto 8085: `GET /salud`, `GET /reportes/estado` y los consumos atípicos de la 403 (402 y la capa 5 agregan rutas) |
| `ia-worker` | `uv run ia-worker` | Consume la cola `ia` (prefetch 10, `ack` manual), aplica las proyecciones, evalúa las salidas (403) y corre las tareas programadas |
| `ia-carga-inicial` | `uv run ia-carga-inicial` | Llena el modelo de lectura con lo que ya existe, por REST (solo `GET`), y termina |

Los tres salen de la misma imagen (`Dockerfile`).

## Casos de uso

### Proyecciones (eventos → `rpt_*`)

| Evento | Qué hace |
|---|---|
| `ObraRegistrada` | Inserta la obra (`SIN_CONTRATO`, avance 0, plazo de hoy); si ya existe, actualiza solo los datos descriptivos (nunca vuelve el estado a `SIN_CONTRATO`) |
| `ContratoRegistrado` | `estado = EN_EJECUCION`; si la obra no existe todavía, falla y se reintenta |
| `PlanillaAprobada` | Bloquea la obra; guarda la planilla `APROBADA` (monto del periodo = Σ líneas, avance del periodo); recalcula el avance desde `rpt_planilla`; completa el cobro (obra, planilla, neto, cliente, zona). Sin obra: falla y se reintenta |
| `PlanillaObservada` | Bloquea la obra si existe; planilla `OBSERVADA` (si no estaba `APROBADA`), `veces_observada + 1`, último motivo y origen. Si la trajo la carga inicial y el evento es anterior o igual a `cargada_en`, no suma |
| `CobroEmitido` | Completa el cobro (número, emisión = fecha de `ocurridoEn` en La Paz, vencimiento, planilla); `estado` = `PENDIENTE`, o `VENCIDO` si ya venció |
| `MovimientoRegistrado` | Inserta el movimiento si no existe; solo si lo insertó y es `SALIDA`, suma cantidad, monto (cantidad × `precioReferencial`, 0 si no viene) y salidas en `rpt_consumo_material` |

Reglas de dominio (`reportes/dominio/indicadores.py`, las mismas que Obras y Finanzas):

- Avance (%) = Σ monto del periodo de las planillas `APROBADA` / monto del contrato × 100, 2 decimales mitad hacia arriba.
- Plazo transcurrido (%) = días desde la fecha de inicio hasta hoy (La Paz) / plazo en días × 100, entre 0 y 100.
- Un cobro `PENDIENTE` pasa a `VENCIDO` al día siguiente de su vencimiento.

### Mensajería

- Inbox: cada evento se aplica en una transacción que inserta `evento_aplicado`; un `id` repetido se confirma sin hacer nada.
- Reintentos: un fallo se vuelve a publicar en la cola `ia` con `x-intentos` + 1 (y `x-error`, `x-clave-origen`); al tercer fallo va a `plataforma.fallidos` con `x-cola-origen`. Van directo a fallidos: cuerpo que no es JSON, sobre con `version` distinta de 1, `datos` incompletos y un `tipo` sin manejador.
- Lector tolerante: los campos desconocidos de `datos` se ignoran; `ubicacion` y `zona` de `ObraRegistrada` y `precioReferencial` de `MovimientoRegistrado` pueden faltar (eventos anteriores al cambio).

### Tareas programadas (America/La_Paz)

| Hora | Tarea |
|---|---|
| 00:20 | Cobros `PENDIENTE` con `vence_en` < hoy → `VENCIDO` |
| 00:30 | Recalcular `plazo_pct` de todas las obras |

### Carga inicial

- Toma `pg_try_advisory_lock` (una sola corrida a la vez; si ya hay una: «Ya hay una carga inicial en curso», código 1). Una corrida anterior que quedó `EN_CURSO` se marca `FALLIDA`.
- Token propio por *client credentials* (`ia-reportes`, rol `gerente`); pide otro cuando falta menos de 30 s para que venza. `X-Request-Id` = `carga_id` en todas las llamadas.
- Pasos: precios del catálogo de Materiales; lista de obras; por cada obra, su detalle, planillas (y cada planilla), cobros y movimientos; con todo en memoria, **una transacción por obra** con las mismas reglas que los eventos («si no existe»). Compara el avance con el de Obras y registra una advertencia si no coincide.
- 10 s por llamada; un reintento tras 1 s ante `5xx`, error de red o tiempo agotado. Si vuelve a fallar, la carga queda `FALLIDA` con el servicio y la obra en `error`; lo cargado queda y la siguiente corrida lo completa.
- Planillas: `APROBADA` con monto y avance del periodo; `OBSERVADA` (y `BORRADOR`, `PRESENTADA` o `APROBANDO` con `observacion`) como la dejaría `PlanillaObservada`, sin periodo ni montos; las demás no se cargan (no llega ningún evento por ellas).
- Aproximaciones (lo que los servicios no exponen): una planilla observada antes de la carga cuenta como máximo 1 vez (`ultimo_origen` vacío), `aprobada_en` vacía, y el precio de los movimientos anteriores es el precio actual del catálogo.

### Consulta

- `GET /reportes/estado` (`admin`): filas por tabla, último evento aplicado de cada tipo y la última carga inicial.

## Qué hace (402): riesgo de retraso

- Modelo XGBoost (`modelos/riesgo_retraso/`, versión de `actual.txt` o `IA_MODELO_RIESGO_VERSION`; carpeta `IA_MODELOS_DIR`) que da la probabilidad de terminar después de `plazo_dias × 1,10`, con nivel `BAJO` < 0,30 ≤ `MEDIO` < 0,60 ≤ `ALTO` y hasta 3 factores (`pred_contribs`). `SIN_DATOS` si la obra no está `EN_EJECUCION` o lleva menos de 5 % del plazo. Sin modelo, no se calcula (se registra el error).
- Variables: `ia.riesgo.dominio.variables.calcular_variables`, la misma función del generador sintético (`ml/riesgo/`). Se leen de `reportes_db` y de `ia_db.consumo_atipico` (403; 0 mientras esté vacía).
- Recalcula (función pública `ServicioRiesgo.recalcular_obra(obra_id, motivo, clave)`): después de `PlanillaAprobada`, `PlanillaObservada` y `ContratoRegistrado` (pasos posteriores del consumidor; un fallo se registra y no reintenta el evento), al terminar `ia-carga-inicial` (todas las obras, `CARGA_INICIAL`) y a las 01:00 de La Paz (obras `EN_EJECUCION`, `DIARIO`). La 403 la llama con `CONSUMO_ATIPICO`.
- Guarda cada cálculo en `ia_db.prediccion` (idempotente por `clave`) y actualiza `rpt_obra_resumen.riesgo_retraso` y `nivel_riesgo`.

## Consumos atípicos (403)

ML no supervisado: un Isolation Forest (scikit-learn, CPU) marca las `SALIDA` de material que se salen del comportamiento habitual. No usa modelo de lenguaje ni bloquea nada: deja un consumo atípico `PENDIENTE` para que una persona lo revise.

- **Detección** (`ia-worker`): paso previo de `MovimientoRegistrado` (antes de la transacción de la 401). Si es `SALIDA` y el evento no está en `evento_aplicado`, lee el historial anterior de `rpt_movimiento` (sin la propia salida), calcula las 13 variables (`atipicos/dominio/variables.py`: las 11 del diseño más `salidas_1h` y `salidas_3h`, que describen las ráfagas), evalúa con el modelo y, si es atípica, guarda `ia_db.consumo_atipico` con `_id = movimientoId` y `$setOnInsert`. Los ingresos no se evalúan. Sin modelo, registra el error y la proyección sigue.
- **Motivos**: de 1 a 3, con reglas sobre las variables y los percentiles del entrenamiento (`atipicos/dominio/motivos.py`).
- **Revisión** (`ia-api`): `NORMAL` o `CONFIRMADO` (confirmar exige comentario de 5 a 300 caracteres), solo `admin` y el residente asignado; actualización atómica que solo cambia un consumo `PENDIENTE`.
- **Modelo**: `ml/atipicos/generar.py` (datos sintéticos) y `ml/atipicos/entrenar.py` (`--fuente sintetica` o `--fuente real` con ≥ 500 salidas), artefactos en `modelos/consumos_atipicos/<version>/` y `actual.txt` (o `IA_MODELO_ATIPICOS_VERSION`). El servicio solo carga archivos de su propio repositorio (`modelo.joblib` usa `pickle`). Reentrenar es manual.
- **Recálculo del riesgo (402)**: después de confirmar la transacción, si la salida fue atípica, se llama al puerto `NotificadorRiesgo.consumo_atipico(obra_id, evento_id)`; su adaptador `RecalculoRiesgo402` (`atipicos/infraestructura/riesgo.py`) llama a `ServicioRiesgo.recalcular_obra(obra_id, motivo=CONSUMO_ATIPICO, clave={obraId}:{eventoId})`. Si falla, se registra y no se reintenta (la tarea diaria de la 402 lo corrige).

## Servidores MCP (501)

El servicio de IA es el **host** de los agentes (502) y también uno de los cuatro **servidores MCP** privados de la plataforma.

- **Servidor** (`ia-api`, `POST /mcp`, HTTP «streamable» sin estado y respuestas JSON): tres herramientas de solo lectura para lo que solo IA sabe: `riesgo_retraso` (última predicción de la 402), `consumos_atipicos` (403) y `consultar_reporte` (el mismo `validar_receta` en modo `MCP` y `MotorReportes` de la 503, con el usuario `ia_reportes_lector`). Solo acepta el rol `agente_ia` (service account de `ia-agentes`); no pasa por el gateway.
- **Cliente** (`ia-worker`): token de `ia-agentes` por *client credentials* (caché hasta 30 s antes de vencer), `tools/list` de los cuatro servidores (Obras, Materiales, Finanzas e IA) al iniciar cada ejecución de agente, 10 s por llamada y `X-Request-Id` = id de la ejecución. Lo que un servidor publica fuera del contrato (u otro esquema) se ignora con una advertencia.
- Argumentos que no cumplen el esquema → `ARGUMENTO_INVALIDO` sin consultar la base; obra inexistente → `OBRA_NO_ENCONTRADA`; base o `ia_db` caídas → `ERROR_INTERNO` (resultados con `isError`, no errores del protocolo).

## Autenticación y visibilidad

- Toda ruta salvo `/salud` exige un token de Keycloak válido (firma por JWKS, vencimiento, emisor `IA_KEYCLOAK_ISSUER`, audiencia `plataforma-api`): sin él, `401 NO_AUTENTICADO`. Si Keycloak no responde al pedir el JWKS: `503 SERVICIO_NO_DISPONIBLE`.
- Roles efectivos según `azp` (convenciones, «Usuario con varios roles»). Las rutas REST atienden solo al ERP: `admin`, `gerente` y `residente`; los demás (también `agente_ia`) reciben `403 SIN_PERMISO`. `/mcp` acepta solo `agente_ia` (501).
- Visibilidad: `admin` y `gerente` ven todas las obras; `residente`, las de `rpt_obra_resumen.residente_id = sub`.

## Qué no hace

- No lee ni escribe las bases de otros servicios, y nadie más escribe en `reportes_db`.
- No publica eventos (en la 401).
- No usa modelos de lenguaje en la capa 4.
- No evalúa consumos atípicos en la carga inicial (403 los evalúa por evento) ni carga obras `CONCLUIDA` (el sistema base no las tiene).
