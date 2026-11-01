# Servicio de IA: contratos

Convenciones comunes (token, Problem Details, `X-Request-Id`, sobre de los eventos, inbox, reintentos): `docs/convenciones.md` de la carpeta general. Si algo cambia aquí, se cambia también en `docs/contratos/` de la carpeta general.

## Expone (REST, puerto 8085)

```
### GET /salud · 401
Sin token · `200 {"estado":"ok"}`

### GET /reportes/estado · 401
Servicio: IA · Quién llama: (pruebas y administración) · Rol: `admin`
Recibe: —
Devuelve: 200 {
  "tablas": { "rptObraResumen": 12, "rptPlanilla": 40, "rptConsumoMaterial": 310, "rptMovimiento": 2210, "rptCobro": 35 },
  "ultimosEventos": [{ "tipo": "PlanillaAprobada", "aplicadoEn": "2026-11-15T14:30:00Z" }],
  "cargaInicial": {
    "cargaId": "uuid", "estado": "TERMINADA", "iniciadaEn": "2026-11-01T13:00:00Z", "terminadaEn": "2026-11-01T13:00:09Z",
    "obras": 3, "filas": { "rptObraResumen": 3, "rptPlanilla": 4, "rptMovimiento": 67, "rptCobro": 3 }, "error": null
  }
}
`cargaInicial`: la última corrida; `null` si nunca se corrió.
Errores: 401 · 403
```

No se expone por GraphQL (es una ayuda para comprobar la proyección y la carga).

```
### GET /predicciones?obraIds=a,b,c · 402
Servicio: IA · Quién llama: Gateway · Roles: `admin`, `gerente`, `residente`
Recibe: hasta 100 ids separados por coma
Devuelve: 200 [{
  "obraId": "uuid", "probabilidad": 0.72, "nivel": "ALTO",
  "factores": [{ "variable": "relacion_ritmo", "descripcion": "El ritmo de avance de los últimos meses es menor al necesario para terminar a tiempo", "peso": 0.81 }],
  "versionModelo": "rr-1.0.0", "calculadaEn": "2026-11-15T05:00:00Z"
}]
Solo las obras visibles para el usuario que tengan predicción, en el orden pedido; las demás se omiten. Con `SIN_DATOS`: "probabilidad": null, "factores": [].
Errores: 400 DEMASIADAS_OBRAS (más de 100) · 400 DATOS_INVALIDOS (sin ids o con un id que no es UUID) · 401 · 403

### GET /obras/{obraId}/prediccion?historial=30 · 402
Servicio: IA · Quién llama: Gateway · Roles: `admin`, `gerente`, `residente` (que pueda ver la obra)
Devuelve: 200 { …mismo objeto…, "historial": [{ "probabilidad": 0.65, "calculadaEn": "…" }] }   (historial de 1 a 90, por defecto 30, del más antiguo al más reciente; omite los cálculos `SIN_DATOS`)
Obra visible sin predicción todavía: 200 con "nivel": "SIN_DATOS", "probabilidad": null, "factores": [], "versionModelo": null, "calculadaEn": null, "historial": []
Errores: 404 OBRA_NO_ENCONTRADA (también si no la puede ver) · 400 DATOS_INVALIDOS (historial fuera de 1 a 90) · 401 · 403

### GET /modelos/riesgo-retraso · 402
Servicio: IA · Quién llama: Gateway · Roles: `admin`, `gerente`, `residente`
Devuelve: 200 {
  "versionModelo": "rr-1.0.0", "entrenadoEn": "2026-11-20T15:00:00Z", "datos": "SINTETICOS", "filasEntrenamiento": 9600,   ("datos": "SINTETICOS" o "REALES")
  "metricas": { "aucRoc": 0.86, "aucRocRegla": 0.74, "recallMedio": 0.83, "precisionAlto": 0.74, "brier": 0.14 },
  "umbrales": { "medio": 0.30, "alto": 0.60 },
  "variables": [{ "nombre": "relacion_ritmo", "descripcion": "Ritmo reciente / ritmo necesario" }]
}
Errores: 503 MODELO_NO_DISPONIBLE · 401 · 403

### GET /consumos-atipicos?obraId=&estado=&desde=&hasta= · 403
Servicio: IA · Quién llama: Gateway · Roles: `admin`, `gerente`, `residente`
Recibe: `obraId` opcional (sin él, todas las obras visibles); `estado` opcional (`PENDIENTE`, `NORMAL`, `CONFIRMADO`; sin él, todos); `desde`/`hasta` opcionales (fechas `AAAA-MM-DD` en La Paz, ambas incluidas, sobre `registradoEn`)
Devuelve: 200 [{
  "id": "uuid-del-movimiento", "movimientoId": "uuid", "obraId": "uuid", "materialId": "id", "partidaId": "uuid",
  "cantidad": 120.0, "periodo": "2026-11", "registradoEn": "2026-11-15T02:40:00Z",
  "puntaje": 0.71, "motivos": ["La cantidad es 4,2 veces la habitual de este material en la obra", "Se registró fuera del horario habitual (22:40)"],
  "estadoRevision": "PENDIENTE", "revisadoPor": null, "revisadoEn": null, "comentario": null,
  "versionModelo": "ca-1.0.0", "detectadoEn": "2026-11-15T02:40:03Z"
}]   (máximo 200, de la más reciente a la más antigua)
Errores: 400 DATOS_INVALIDOS (estado o fechas inválidos, o `desde` > `hasta`) · 404 OBRA_NO_ENCONTRADA (también si no la puede ver) · 401 · 403

### GET /consumos-atipicos/resumen?obraIds=a,b,c · 403
Servicio: IA · Quién llama: Gateway · Roles: `admin`, `gerente`, `residente`
Devuelve: 200 [{ "obraId": "uuid", "pendientes": 2 }]   (solo obras visibles, en el orden pedido; las que no tienen pendientes vienen con 0)
Errores: 400 DEMASIADAS_OBRAS (más de 100 distintas) · 401 · 403

### PUT /consumos-atipicos/{id}/revision · 403
Servicio: IA · Quién llama: Gateway · Roles: `admin`, `residente` asignado a la obra
Recibe: { "estado": "CONFIRMADO", "comentario": "Se usó en la losa de otra obra sin registrarlo" }
Devuelve: 200 con el consumo atípico actualizado (`revisadoPor` = `sub`, `revisadoEn`, `comentario` sin espacios a los lados)
Errores: 400 DATOS_INVALIDOS (estado `PENDIENTE`, desconocido o falta) · 404 CONSUMO_ATIPICO_NO_ENCONTRADO (también si el residente no está asignado a su obra) · 409 REVISION_YA_REGISTRADA · 422 COMENTARIO_INVALIDO (falta en `CONFIRMADO` o fuera de 5 a 300 caracteres; `errores[]` con `campo` = `comentario` y la regla en `mensaje`) · 403 (por ejemplo `gerente`) · 401
```

La revisión es una actualización atómica (`find_one_and_update` con `estado_revision = PENDIENTE`); si el consumo existe pero ya no está pendiente, `409`.

Las consultas del riesgo leen MongoDB (`prediccion`) y `reportes_db` (visibilidad); no calculan nada en la petición.

### Reportes por voz: constructor de reportes (503)

Los tres son solo para `admin` y `gerente` (los reportes cruzan todas las obras); `residente` recibe `403`. Detalle del catálogo, la receta y los atajos: `docs/specs/503-reportes-voz/diseno.md` de la carpeta general.

```
### POST /consultas-reporte · 503
Servicio: IA · Quién llama: Gateway (60 s, sin reintento) · Roles: `admin`, `gerente`
Recibe exactamente uno de `texto` (3 a 500 caracteres) o `atajo`; `directorio` lo agrega el gateway (opcional; hasta 1000 clientes):
  { "texto": "¿Cuánto cemento se usó en Las Palmas este trimestre?", "directorio": { "clientes": [{ "id": "uuid-keycloak", "nombre": "Constructora Andina SRL" }] } }
  { "atajo": "COBROS_POR_VENCER", "directorio": { "clientes": [] } }
Devuelve: 200 {
  "id": "uuid", "texto": "…", "atajo": null, "conjunto": "CONSUMO_MATERIAL", "tituloConjunto": "Material usado",
  "receta": { "conjunto": "CONSUMO_MATERIAL", "filtros": […], "agruparPor": ["obra", "material"], "metricas": […], "orden": […], "limite": 100 },
  "aplicado": [{ "nombre": "Obra", "valor": "contiene «Las Palmas»" }],
  "resumen": "…", "resumenPlantilla": false, "avisos": [],
  "columnas": [{ "clave": "obra", "titulo": "Obra", "tipo": "TEXTO" }],
  "filas": [["Edificio Las Palmas", "Cemento IP-30", "bolsa", "1240.000", "76880.00", "31"]],
  "totalFilas": 1, "truncado": false, "creadaEn": "2027-03-20T14:00:00Z"
}
`tipo` de columna: TEXTO, NUMERO, MONTO, PORCENTAJE, FECHA, BOOLEANO. Filas como texto (números con punto decimal y sin separador de miles, fechas ISO, booleanos true/false, nulos null); hasta 100 filas (`truncado` si hay más).
Atajos: AVANCE_OBRAS, OBRAS_EN_RIESGO, CONSUMO_MATERIAL_MES, SALIDAS_SIN_STOCK, COBROS_VENCIDOS, COBROS_POR_VENCER, COBRADO_POR_OBRA, PLANILLAS_OBSERVADAS, PLANILLAS_APROBADAS, CONSUMOS_ATIPICOS_PENDIENTES (sin modelo de lenguaje para entender el pedido).
Errores: 400 TEXTO_INVALIDO (largo fuera de 3 a 500) · 400 DATOS_INVALIDOS (ni `texto` ni `atajo`, los dos, atajo desconocido o `directorio` mal formado) · 422 INTENCION_NO_RECONOCIDA · 422 RECETA_INVALIDA (`errores[]` con `campo` = ruta en la receta y `mensaje` = la regla) · 429 DEMASIADAS_CONSULTAS (10 por minuto por usuario, con `Retry-After`) · 503 LLM_NO_DISPONIBLE (el modelo no responde al interpretar; nunca con un atajo) · 503 REPORTES_NO_DISPONIBLES (no responde `reportes_db` o `ia_db`, o la consulta pasa de 5 s) · 503 SERVICIO_NO_DISPONIBLE (Materiales con filtro de material, o sin directorio con filtro de cliente) · 504 TIEMPO_AGOTADO (más de 50 s dentro de IA) · 401 · 403
Todos los casos, salvo 400, 401, 403 y 429, guardan `consulta_reporte`. El límite se cuenta antes de validar el cuerpo.

### GET /consultas-reporte?limite= · 503
Servicio: IA · Quién llama: Gateway · Roles: `admin`, `gerente`
Las del usuario del token, más recientes primero; `limite` 1 a 20 (por defecto 10).
Devuelve: 200 [{ "id", "texto", "atajo", "conjunto", "resultado", "resumen", "totalFilas", "creadaEn" }]   (`resultado`: OK, NO_RECONOCIDA, RECETA_INVALIDA, ERROR)
Errores: 400 DATOS_INVALIDOS (`limite` fuera de 1 a 20) · 401 · 403

### GET /catalogo-reportes · 503
Servicio: IA · Quién llama: Gateway · Roles: `admin`, `gerente`
Devuelve: 200 {
  "conjuntos": [{ "conjunto": "CONSUMO_MATERIAL", "titulo": "Material usado", "descripcion": "…",
                  "columnas": [{ "clave": "material", "titulo": "Material", "tipo": "TEXTO" }],
                  "filtros": ["obra", "zona", "cliente", "material", "periodo"], "agrupaciones": ["obra", "zona", "cliente", "material", "periodo"],
                  "ejemplos": ["¿Cuánto cemento se usó en Las Palmas este trimestre?"] }],
  "atajos": [{ "atajo": "COBROS_POR_VENCER", "titulo": "Cobros que vencen en los próximos 30 días", "descripcion": "…" }]
}   (el catálogo del modo REST)
Errores: 401 · 403
```

- El modelo de lenguaje (Ollama o Anthropic, `IA_LLM_PROVEEDOR`) solo devuelve una **receta**; el código la valida contra el catálogo y arma la consulta con SQLAlchemy Core y valores enlazados (usuario `ia_reportes_lector`, `READ ONLY`, 5 s). Nunca ejecuta SQL del modelo.

- Toda ruta salvo `/salud` exige token (`401 NO_AUTENTICADO`; `503 SERVICIO_NO_DISPONIBLE` si Keycloak no entrega el JWKS). Solo `admin`, `gerente` y `residente`; los demás roles (también `agente_ia`) reciben `403 SIN_PERMISO` en todas las rutas REST. Una ruta que no existe: `404 RUTA_NO_ENCONTRADA` (con token válido). **Excepción:** `/mcp` acepta **solo** `agente_ia` (501).

### Agentes (502)

Tres agentes fijos (`SEGUIMIENTO_OBRAS`, `CONTROL_MATERIALES`, `CONTROL_COBROS`) que corren en `ia-worker` a las 06:00 (La Paz) y a pedido. JSON en `camelCase`. Para `residente`: solo sus obras (`rpt_obra_resumen.residente_id`) y nunca `CONTROL_COBROS`. Detalle: `docs/specs/502-agentes-erp/diseno.md` de la carpeta general.

```
### GET /agentes · 502
Roles: admin, gerente, residente
Devuelve: 200 [{ "agente", "nombre", "mision", "herramientas": [..], "ultimaEjecucion": EjecucionCorta | null, "sugerenciasPendientes": 2 }]
Para residente no aparece CONTROL_COBROS y sugerenciasPendientes cuenta solo sus obras.

### POST /agentes/{agente}/ejecuciones · 502
Roles: admin, gerente · Sin cuerpo
Devuelve: 202 EjecucionCorta { "id", "agente", "origen": "MANUAL", "estado": "EN_CURSO", "iniciadaEn", "terminadaEn": null, "cantidadHallazgos": 0 }
Errores: 400 AGENTE_INVALIDO · 409 EJECUCION_EN_CURSO · 401 · 403

### GET /ejecuciones?agente=&estado=&limite= · 502
Roles: admin, gerente · limite 1 a 50 (20) · Devuelve: 200 [EjecucionCorta] (más recientes primero)
Errores: 400 DATOS_INVALIDOS (agente, estado o limite) · 401 · 403

### GET /ejecuciones/{id} · 502
Roles: admin, gerente
Devuelve: 200 { "id", "agente", "origen", "estado", "solicitadaPor", "iniciadaEn", "terminadaEn", "proveedorLlm", "modelo", "resumen", "error": {codigo, mensaje} | null,
  "pasos": [{ "numero", "servidor", "herramienta", "argumentos" (texto JSON), "resultadoResumen", "en", "duracionMs", "error" }],
  "hallazgos": [{ "obraId", "obraNombre", "tipo", "severidad", "situacion", "causaProbable", "evidencia": [{ "herramienta", "dato" }] }] }
Errores: 404 EJECUCION_NO_ENCONTRADA · 401 · 403

### GET /hallazgos?agente=&obraId= · 502
Roles: admin, gerente, residente (sus obras; sin CONTROL_COBROS)
Hallazgos vigentes (los de la última ejecución TERMINADA de cada agente), cada uno con además "ejecucionId", "agente" y "detectadoEn" (= terminadaEn).
Errores: 400 DATOS_INVALIDOS (agente) · 401 · 403

### GET /sugerencias?estado=&agente=&obraId=&limite= · 502
Roles: admin, gerente, residente (sus obras; sin CONTROL_COBROS) · limite 1 a 100 (50) · más recientes primero
Devuelve: 200 [{ "id", "ejecucionId", "agente", "obraId", "obraNombre", "accion", "accionTexto", "descripcion", "estado", "creadaEn", "decididaPor", "decididaEn", "comentario" }]
Errores: 400 DATOS_INVALIDOS · 401 · 403

### POST /sugerencias/{id}/aprobar · 502
Roles: admin, gerente, residente (sus obras; sin CONTROL_COBROS) · Recibe { "comentario": "opcional, hasta 300" }
Devuelve: 200 la sugerencia (APROBADA, decididaPor = sub, decididaEn). Solo registra la decisión: no llama a otros servicios ni publica eventos.
Errores: 404 SUGERENCIA_NO_ENCONTRADA (también si no puede verla) · 409 SUGERENCIA_YA_DECIDIDA · 422 COMENTARIO_INVALIDO (errores[] con campo = comentario) · 401 · 403

### POST /sugerencias/{id}/rechazar · 502
Mismos roles · Recibe { "motivo": "10 a 500 caracteres" } · Devuelve: 200 (RECHAZADA; el motivo queda en comentario)
Errores: 404 SUGERENCIA_NO_ENCONTRADA · 409 SUGERENCIA_YA_DECIDIDA · 422 MOTIVO_INVALIDO (errores[] con campo = motivo) · 401 · 403
```

## Herramientas MCP (capa 5, solo lectura) · 501

```
Ruta: POST /mcp (MCP, HTTP «streamable», sin estado, respuestas JSON; versión común 2025-11-25) · Rol: agente_ia (service account de ia-agentes) · Solo lo llama el servicio de IA
```

- `/mcp` sin token o con un token inválido → `401 NO_AUTENTICADO`; token válido sin `agente_ia`, o con `agente_ia` pero de otro cliente (`azp` distinto de `ia-agentes`) → `403 SIN_PERMISO` (Problem Details). `GET /mcp` → `405` (sin flujo SSE).
- `tools/list` devuelve exactamente las tres herramientas, cada una con descripción, `inputSchema` y `outputSchema` (`additionalProperties: false`) y `annotations: { readOnlyHint: true, openWorldHint: false }`. Sin *resources* ni *prompts*.
- `structuredContent` cumple el `outputSchema`; el mismo JSON va como texto en `content`.
- Errores de herramienta (resultado con `isError: true` y texto JSON `{"codigo", "mensaje"}`): `ARGUMENTO_INVALIDO` (no cumple el esquema; no consulta la base), `OBRA_NO_ENCONTRADA`, `ERROR_INTERNO`. Una herramienta que no existe es un error del protocolo.
- Cada llamada deja una línea en el registro con `herramienta`, `argumentos`, `filas`, `duracionMs` y `requestId` (`X-Request-Id` = id de la ejecución del agente). Nunca el token.

| Herramienta | Entrada | Salida | Fuente |
|---|---|---|---|
| `riesgo_retraso` | `{obraIds?: uuid[1..50], niveles?: (BAJO\|MEDIO\|ALTO\|SIN_DATOS)[], limite?: 1..50 = 50}` | `{generadoEn, predicciones[{obraId, obraNombre, probabilidad, nivel, factores[{variable, descripcion, peso}], versionModelo, calculadaEn}], total, truncado}` | Obras `EN_EJECUCION` de `rpt_obra_resumen` con su última predicción (`ia_db.prediccion`, 402); probabilidad descendente, `SIN_DATOS` al final. Sin modelo o sin predicciones: `[]`. `OBRA_NO_ENCONTRADA` si un id no está en `rpt_obra_resumen` |
| `consumos_atipicos` | `{obraId?: uuid, estados?: (PENDIENTE\|NORMAL\|CONFIRMADO)[] = [PENDIENTE], dias?: 1..90 = 30, limite?: 1..50 = 50}` | `{consumos[{movimientoId, obraId, obraNombre, materialId, partidaId, cantidad, periodo, registradoEn, puntaje, motivos[], estadoRevision}], total, truncado}` | `ia_db.consumo_atipico` (403) con `registradoEn` en los últimos `dias` (La Paz); puntaje descendente; cada motivo hasta 200 caracteres. `OBRA_NO_ENCONTRADA` si `obraId` no está en `rpt_obra_resumen` |
| `consultar_reporte` | La receta de la 503 en modo `MCP` en el primer nivel: `{conjunto, columnas?, filtros?, agruparPor?, metricas?, orden?, limite?: 1..50 = 20}` (filtros `obraId`, `materialId`, `clienteId` por id; sin `material` ni `cliente` por nombre; sin `NINGUNO`) | `{conjunto, titulo, aplicado[{nombre, valor}], columnas[{clave, titulo, tipo}], filas[[texto\|null]], totalFilas, truncado, avisos[]}` (las columnas `material` y `cliente` traen el id) | Mismo `validar_receta` y `MotorReportes` de la 503 (usuario `ia_reportes_lector`). `ARGUMENTO_INVALIDO` con hasta 5 problemas, cada uno con su ruta; `ERROR_INTERNO` si `reportes_db` o `ia_db` no responden o la consulta pasa de 5 s |


## Escucha (cola `ia`, exchange `plataforma`)

La cola durable `ia` y sus enlaces están en las definiciones de RabbitMQ de `infra` desde la fase 1 (existen aunque el servicio no esté corriendo); el worker además los declara al conectarse.

| Clave de enrutamiento | Publica | `datos` que usa | Proyección |
|---|---|---|---|
| `evento.obras.ObraRegistrada` | Obras | `obraId, nombre, ubicacion, zona, fechaInicio, asignaciones{residenteId, maestroId, almaceneroId, clienteId}, contrato{monto, plazoDias}` (`ubicacion` y `zona` desde la 401; pueden faltar) | `rpt_obra_resumen` (alta) |
| `evento.finanzas.ContratoRegistrado` | Finanzas | `obraId` | `rpt_obra_resumen.estado = EN_EJECUCION` |
| `evento.obras.PlanillaAprobada` | Obras | `planillaId, obraId, periodo, neto, cobroId, lineas[{monto}]` (+ `ocurridoEn` del sobre) | `rpt_planilla`, avance de `rpt_obra_resumen`, `rpt_cobro` |
| `evento.obras.PlanillaObservada` | Obras | `planillaId, obraId, motivo, origen` (+ `ocurridoEn`) | `rpt_planilla` (veces observada, último motivo) |
| `evento.finanzas.CobroEmitido` | Finanzas | `planillaId, cobroId, numero, venceEn` (+ `ocurridoEn` = emisión) | `rpt_cobro` |
| `evento.materiales.MovimientoRegistrado` | Materiales | `movimientoId, obraId, materialId, tipo, cantidad, partidaId, periodo, superaStock, registradoEn, precioReferencial` (`precioReferencial` desde la 401; puede faltar) | `rpt_movimiento` y, si es `SALIDA`, `rpt_consumo_material` |

- Sobre común, `version` 1. Otra versión, cuerpo que no es JSON, `datos` incompletos o un `tipo` sin manejador → directo a `plataforma.fallidos`.
- Reintentos en la misma cola con `x-intentos`, `x-error` y `x-clave-origen`; al tercer fallo, a `plataforma.fallidos` con `x-cola-origen = ia`.
- Campos desconocidos en `datos` se ignoran (lector tolerante).
- `MovimientoRegistrado` con `tipo = SALIDA` además se evalúa como posible consumo atípico (403), antes de la proyección.
- Publica: nada (en la 401 ni en la 403).

## Consume (REST, solo en la carga inicial)

Con el token del cliente confidencial `ia-reportes` (*client credentials*, rol de realm `gerente`), pedido a `IA_KEYCLOAK_ISSUER/protocol/openid-connect/token`. Solo `GET`; 10 s por llamada y un reintento tras 1 s ante `5xx`, error de red o tiempo agotado. Cada corrida manda su `carga_id` como `X-Request-Id`.

| Paso | Servicio · endpoint | Para qué |
|---|---|---|
| 1 | Materiales `GET /materiales?pagina=N&tamano=100` (todas las páginas) | `precioReferencial` actual de cada material |
| 2 | Obras `GET /obras` | Lista de obras (`id`) |
| 3 | Obras `GET /obras/{id}` | `nombre`, `ubicacion`, `zona`, `estado`, `fechaInicio`, `asignaciones`, `contrato.monto`, `contrato.plazoDias`, `avancePct` (para comprobar) |
| 4 | Obras `GET /obras/{id}/planillas` | Planillas de la obra con `estado` |
| 5 | Obras `GET /planillas/{id}` (cada planilla del paso 4) | `periodo`, `neto`, `lineas[].monto`, `observacion`, `cobroId` |
| 6 | Finanzas `GET /cobros?obraId={id}&pagina=N&tamano=100` (todas las páginas) | `id`, `numero`, `planillaId`, `montoNeto`, `emitidoEn`, `venceEn`, `estado` |
| 7 | Materiales `GET /obras/{id}/movimientos?pagina=N&tamano=100&orden=registradoEn&direccion=asc` (todas las páginas) | `id`, `materialId`, `tipo`, `cantidad`, `partidaId`, `periodo`, `superaStock`, `registradoEn` |

Un `404 OBRA_NO_ENCONTRADA` de Materiales en el paso 7 se toma como «sin movimientos».

## Consume (REST, reportes por voz · 503)

| Servicio · endpoint | Token | Para qué |
|---|---|---|
| Materiales `GET /materiales?pagina=N&tamano=100` (todas las páginas; `IA_MATERIALES_URL`) | El del usuario que reenvía el gateway | Nombres y unidades de los materiales en las filas, y el filtro `material` (por `nombre` o `codigo`, sin distinguir mayúsculas ni tildes). Caché de 5 minutos, 3 s por llamada. No alimenta la base de reportes. Si no responde: con filtro `material`, `503 SERVICIO_NO_DISPONIBLE`; sin él, el id y el aviso «Nombres de materiales no disponibles» |

Los nombres de los clientes no se piden a nadie: los trae el gateway en `directorio.clientes` (el servicio de IA no llama a Keycloak).

## Consume (modelo de lenguaje · 503)

| Proveedor (`IA_LLM_PROVEEDOR`) | Cliente | Para qué |
|---|---|---|
| `ollama` (por defecto) | `ollama` 0.6 (`AsyncClient(host=IA_OLLAMA_URL)`), modelo `IA_LLM_MODELO` (por defecto `qwen3:8b`) | Interpretar el pedido (receta, 20 s) y redactar el resumen (25 s) |
| `anthropic` | `anthropic` 2 (`AsyncAnthropic`, Messages API), clave `IA_ANTHROPIC_API_KEY`, modelo `IA_LLM_MODELO` (por defecto `claude-sonnet-5-5`) | Ídem. Con este proveedor, el texto del usuario y los datos delimitados salen a la API de Anthropic |

## Consume (MCP, host de los agentes · 501)

El proceso `ia-worker` es el único cliente MCP. Token del cliente confidencial `ia-agentes` (*client credentials* contra `IA_KEYCLOAK_ISSUER/protocol/openid-connect/token`, rol de realm `agente_ia`; el secreto `IA_AGENTES_CLIENT_SECRET` solo lo lee `ia-worker`), reutilizado hasta 30 s antes de que venza. Encabezados `Authorization: Bearer …` y `X-Request-Id: <ejecucionId>`; 10 s por llamada; `tools/list` de cada servidor al iniciar cada ejecución de agente. Una herramienta que no está en este contrato, o con otro esquema de entrada, se ignora con una advertencia en el registro.

| Servidor (variable) | Herramientas |
|---|---|
| Obras (`IA_OBRAS_MCP_URL`, por defecto `http://localhost:8081/mcp`) | `avance_obra {obraId?, periodo?}`, `buscar_diario {obraId, desde?, hasta?, texto?, limite?}`, `planillas_estado {obraId?, estados?, desdePeriodo?, limite?}` |
| Materiales (`IA_MATERIALES_MCP_URL`, por defecto `http://localhost:8082/mcp`) | `stock_obra {obraId?, estados?, limite?}`, `consumos_partida {obraId, periodo, partidaId?}` |
| Finanzas (`IA_FINANZAS_MCP_URL`, por defecto `http://localhost:8083/mcp`) | `cobros_por_vencer {dias?, incluirVencidos?, obraId?}` |
| IA (`IA_PROPIO_MCP_URL`, por defecto `http://localhost:8085/mcp`; en Compose `http://ia-api:8085/mcp`) | `consultar_reporte`, `riesgo_retraso`, `consumos_atipicos` (las de arriba) |

Los agentes de la 502 las usan con mínimo privilegio: cada agente solo ve sus herramientas y, en `consultar_reporte`, sus conjuntos (Seguimiento de obras: `OBRAS`, `PLANILLAS`; Control de materiales: `CONSUMO_MATERIAL`, `MOVIMIENTOS`; Control de cobros: `COBROS`, `PLANILLAS`). La visibilidad del residente sale de `rpt_obra_resumen.residente_id` (401); no se llama a Obras.

Esquemas y salidas de cada herramienta: `docs/specs/501-servidores-mcp/diseno.md` de la carpeta general.
