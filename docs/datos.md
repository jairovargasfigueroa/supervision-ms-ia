# Servicio de IA: datos

## `reportes_db` (PostgreSQL, misma instancia del puerto 5433, usuario propio `ia`)

Modelo de lectura (CQRS) de la 401. Solo lo escribe este servicio (proyecciones de eventos y carga inicial). Migraciones con Alembic (`migraciones/`).

### rpt_obra_resumen
| Campo | Tipo | Notas |
|---|---|---|
| obra_id | uuid | PK |
| nombre | varchar | |
| ubicacion | varchar | null si el evento no la trae |
| zona | varchar | `NORTE`, `SUR`, `ESTE`, `OESTE`, `CENTRO` o null |
| cliente_id | uuid | ref Keycloak |
| residente_id | uuid | ref Keycloak; visibilidad |
| maestro_id | uuid | ref Keycloak |
| almacenero_id | uuid | ref Keycloak |
| estado | varchar | `SIN_CONTRATO`, `EN_EJECUCION` |
| fecha_inicio | date | |
| plazo_dias | int | |
| monto_contrato | numeric(14,2) | |
| avance_pct | numeric(5,2) | misma regla que Obras |
| plazo_pct | numeric(5,2) | se recalcula a diario |
| riesgo_retraso | numeric(4,3) | probabilidad 0 a 1 (402); null sin predicción |
| nivel_riesgo | varchar | `BAJO`, `MEDIO`, `ALTO`, `SIN_DATOS` (402) |
| actualizado_en | timestamptz | |

Índice: `residente_id`.

### rpt_planilla
| Campo | Tipo | Notas |
|---|---|---|
| planilla_id | uuid | PK |
| obra_id | uuid | índice |
| periodo | char(7) | `AAAA-MM`; null si solo se conoce por una observación |
| estado | varchar | `APROBADA`, `OBSERVADA` |
| neto | numeric(14,2) | |
| monto_periodo | numeric(14,2) | Σ montos de las líneas |
| avance_periodo_pct | numeric(5,2) | monto_periodo / monto_contrato × 100 |
| veces_observada | int | por defecto 0 |
| ultimo_motivo | text | |
| ultimo_origen | varchar | `CLIENTE`, `SAGA` |
| aprobada_en | timestamptz | `ocurridoEn` de `PlanillaAprobada`; null si la trajo la carga inicial |
| cargada_en | timestamptz | momento en que la carga inicial leyó la planilla; null si solo llegó por eventos |
| actualizado_en | timestamptz | |

### rpt_consumo_material
| Campo | Tipo | Notas |
|---|---|---|
| obra_id | uuid | PK (compuesta) |
| periodo | char(7) | PK |
| material_id | varchar | PK, ref Materiales |
| partida_id | uuid | PK, ref Obras |
| cantidad | numeric(14,3) | suma de salidas |
| monto | numeric(14,2) | suma de cantidad × precio referencial |
| salidas | int | número de salidas |
| actualizado_en | timestamptz | |

### rpt_movimiento
| Campo | Tipo | Notas |
|---|---|---|
| movimiento_id | uuid | PK |
| obra_id | uuid | |
| material_id | varchar | |
| partida_id | uuid | null en ingresos |
| tipo | varchar | `INGRESO`, `SALIDA` |
| cantidad | numeric(14,3) | |
| precio_referencial | numeric(14,2) | null en eventos anteriores al cambio; en la carga inicial, el precio del catálogo al cargar |
| periodo | char(7) | |
| supera_stock | boolean | |
| registrado_en | timestamptz | |

Índices: (`obra_id`, `material_id`, `registrado_en`), (`material_id`, `tipo`) (los usa la 403).

### rpt_cobro
| Campo | Tipo | Notas |
|---|---|---|
| cobro_id | uuid | PK |
| numero | varchar | de `CobroEmitido` |
| obra_id | uuid | de `PlanillaAprobada` |
| planilla_id | uuid | |
| cliente_id | uuid | copia de la obra |
| zona | varchar | copia de la obra |
| monto | numeric(14,2) | neto de la planilla |
| emitido_en | date | fecha de `ocurridoEn` de `CobroEmitido` en La Paz |
| vence_en | date | |
| estado | varchar | `PENDIENTE`, `VENCIDO` |
| actualizado_en | timestamptz | |

Las columnas que llegan por eventos distintos pueden estar null hasta que llega el otro evento.

### evento_aplicado (inbox)
| Campo | Tipo | Notas |
|---|---|---|
| evento_id | uuid | PK; `id` del sobre |
| tipo | varchar | índice (`tipo`, `aplicado_en`) |
| aplicado_en | timestamptz | |

### carga_inicial
| Campo | Tipo | Notas |
|---|---|---|
| carga_id | uuid | PK; también es el `X-Request-Id` de sus llamadas |
| iniciada_en | timestamptz | |
| terminada_en | timestamptz | null mientras corre |
| estado | varchar | `EN_CURSO`, `TERMINADA`, `FALLIDA` |
| obras | int | obras procesadas |
| filas | jsonb | filas insertadas por tabla (`{"rptObraResumen": 3, "rptPlanilla": 4, "rptMovimiento": 67, "rptCobro": 3}`) |
| error | text | servicio, obra y causa si falló |

Una corrida que se cortó sin terminar queda `EN_CURSO`; la siguiente, que ya tomó el bloqueo, la marca `FALLIDA` al empezar.

## `ia_db` (MongoDB)

Conexión con `IA_MONGO_URL` e `IA_MONGO_DB`; cada módulo registra sus índices y `ia-api` e `ia-worker` los aseguran al arrancar. La 403 agrega `consumo_atipico` y la capa 5 las de los agentes.

### prediccion (402)
| Campo | Tipo | Notas |
|---|---|---|
| _id | ObjectId | |
| clave | string | único (`ux_prediccion_clave`): `{obraId}:{eventoId}`, `{obraId}:diario:{AAAA-MM-DD}` o `{obraId}:carga:{cargaId}` (idempotencia: se inserta con `$setOnInsert`) |
| obra_id | string | índice (`obra_id`, `calculada_en` descendente) |
| riesgo_retraso | double | probabilidad 0 a 1; null si `SIN_DATOS` |
| nivel | string | `BAJO`, `MEDIO`, `ALTO`, `SIN_DATOS` |
| factores | arreglo de `{ variable, descripcion, peso }` | hasta 3 |
| variables | objeto | valores usados (para auditar) |
| version_modelo | string | `rr-1.0.0` |
| motivo | string | `PLANILLA_APROBADA`, `PLANILLA_OBSERVADA`, `CONTRATO_REGISTRADO`, `CONSUMO_ATIPICO` (403), `CARGA_INICIAL` (401), `DIARIO` |
| calculada_en | date | |

La 402 escribe también `rpt_obra_resumen.riesgo_retraso` y `nivel_riesgo`.

### consumo_atipico (403)

Una `SALIDA` que el Isolation Forest marcó como atípica. La escribe `ia-worker` al aplicar `MovimientoRegistrado` (paso previo a la transacción de la 401, `$setOnInsert` por `_id`: idempotente); la revisión la cambia `ia-api` (`PUT /consumos-atipicos/{id}/revision`).

| Campo | Tipo | Notas |
|---|---|---|
| _id | string | `movimiento_id` (ref Materiales) |
| obra_id | string | índice `ix_consumo_atipico_obra_estado_registrado` (`obra_id`, `estado_revision`, `registrado_en` descendente) |
| material_id | string | ref Materiales |
| partida_id | string | ref Obras |
| cantidad | decimal | `Decimal128` |
| periodo | string | `AAAA-MM` |
| registrado_en | date | de la salida |
| puntaje | double | 0 a 1 (`−score_samples`) |
| motivos | arreglo de string | 1 a 3 |
| variables | objeto | valores usados por el modelo (para auditar) |
| version_modelo | string | `ca-1.0.0` |
| detectado_en | date | |
| estado_revision | string | `PENDIENTE`, `NORMAL`, `CONFIRMADO` |
| revisado_por | string | ref Keycloak (`sub`); null hasta la revisión |
| revisado_en | date | null hasta la revisión |
| comentario | string | null si no se escribió |

El historial que usan las variables sale de `rpt_movimiento` (solo lectura: salidas con `registrado_en` anterior y otro `movimiento_id`).

### consulta_reporte (503)

Cada consulta de reporte de las personas (`POST /consultas-reporte`), también las no reconocidas. La escribe `ia-api`. No se guardan las filas (se pueden volver a pedir).

| Campo | Tipo | Notas |
|---|---|---|
| _id | string (uuid) | |
| usuario_id | string | `sub` del token; índice `ix_consulta_reporte_usuario_creada` (`usuario_id`, `creada_en` descendente) |
| texto | string | lo que envió el usuario; nulo si fue un atajo |
| atajo | string | código del atajo; nulo si fue texto |
| conjunto | string | nulo si no se reconoció |
| receta | objeto | receta aplicada (con valores por defecto); nula si no se reconoció |
| resumen | string | redactado o de plantilla; nulo si hubo error |
| resumen_plantilla | bool | |
| resultado | string | `OK`, `NO_RECONOCIDA`, `RECETA_INVALIDA`, `ERROR` |
| error_codigo | string | código del error si `ERROR` |
| total_filas | int | |
| proveedor_llm · modelo | string | `ollama` / `qwen3:8b`, `anthropic` / `claude-sonnet-5-5` |
| duracion_ms | int | |
| creada_en | date | |

### limite_consulta (503)

Límite de 10 consultas de reporte por minuto por usuario (ventana fija de un minuto, decisión del equipo).

| Campo | Tipo | Notas |
|---|---|---|
| _id | string | `{usuarioId}:{AAAA-MM-DDTHH:MM}` (minuto en UTC) |
| usuario_id | string | |
| cuenta | int | |
| expira_en | date | fin del minuto + 2 min; índice TTL `ttl_limite_consulta_expira` (`expireAfterSeconds: 0`) |

Cada pedido hace `find_one_and_update(_id, {$inc: {cuenta: 1}, $setOnInsert: {…}}, upsert)`: atómico, sirve con varios procesos de `ia-api`. Con `cuenta > 10` → `429 DEMASIADAS_CONSULTAS` con `Retry-After`.

## Usuario de solo lectura de `reportes_db` (503)

Rol `ia_reportes_lector` (`LOGIN`, `default_transaction_read_only = on`; lo crea el script de inicio de PostgreSQL de `infra`, contraseña en `.env`) con `SELECT` **solo** en `rpt_obra_resumen`, `rpt_consumo_material`, `rpt_movimiento`, `rpt_cobro` y `rpt_planilla` (no en `evento_aplicado` ni `carga_inicial`). Los `GRANT` van en la migración `0002_lector_reportes` de este servicio (si el rol no existe, la migración falla: hay que crearlo antes). Lo usa el constructor de reportes (`IA_REPORTES_DB_URL_LECTURA`), con transacción `READ ONLY` y `statement_timeout` de 5 s.
