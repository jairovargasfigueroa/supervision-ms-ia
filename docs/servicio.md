# Servicio de IA: responsabilidades

## Qué hace (401)

**Mecanismo: CQRS solo para lo que cruza servicios.** Obras, Materiales y Finanzas siguen escribiendo y leyendo lo suyo. Este servicio arma un **modelo de lectura** desnormalizado (`reportes_db`, tablas `rpt_*`) con proyecciones de los eventos de RabbitMQ, para que las predicciones (402, 403) y los reportes por voz (capa 5) no consulten las bases de los demás. No hay *event sourcing*: el modelo se puede volver a armar con la carga inicial. Consistencia eventual: va unos segundos detrás de los servicios, y el plazo y el vencimiento de cobros se recalculan una vez al día.

| Proceso | Comando | Qué hace |
|---|---|---|
| `ia-api` | `uv run ia-api` | FastAPI en el puerto 8085: `GET /salud` y `GET /reportes/estado` (402, 403 y la capa 5 agregan rutas) |
| `ia-worker` | `uv run ia-worker` | Consume la cola `ia` (prefetch 10, `ack` manual), aplica las proyecciones y corre las tareas programadas |
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
- Aproximaciones (lo que los servicios no exponen): una planilla observada antes de la carga cuenta como máximo 1 vez (`ultimo_origen` vacío), `aprobada_en` vacía, y el precio de los movimientos anteriores es el precio actual del catálogo.

### Consulta

- `GET /reportes/estado` (`admin`): filas por tabla, último evento aplicado de cada tipo y la última carga inicial.

## Autenticación y visibilidad

- Toda ruta salvo `/salud` exige un token de Keycloak válido (firma por JWKS, vencimiento, emisor `IA_KEYCLOAK_ISSUER`, audiencia `plataforma-api`): sin él, `401 NO_AUTENTICADO`. Si Keycloak no responde al pedir el JWKS: `503 SERVICIO_NO_DISPONIBLE`.
- Roles efectivos según `azp` (convenciones, «Usuario con varios roles»). El servicio atiende solo al ERP: `admin`, `gerente` y `residente`; los demás reciben `403 SIN_PERMISO` en todo el servicio.
- Visibilidad: `admin` y `gerente` ven todas las obras; `residente`, las de `rpt_obra_resumen.residente_id = sub`.

## Qué no hace

- No lee ni escribe las bases de otros servicios, y nadie más escribe en `reportes_db`.
- No publica eventos (en la 401).
- No usa modelos de lenguaje en la capa 4.
- No evalúa consumos atípicos en la carga inicial (403 los evalúa por evento) ni carga obras `CONCLUIDA` (el sistema base no las tiene).
