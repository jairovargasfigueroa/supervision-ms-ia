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
    "obras": 3, "filas": { "rptObraResumen": 3, "rptPlanilla": 5, "rptMovimiento": 67, "rptCobro": 3 }, "error": null
  }
}
`cargaInicial`: la última corrida; `null` si nunca se corrió.
Errores: 401 · 403
```

No se expone por GraphQL (es una ayuda para comprobar la proyección y la carga).

```
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

- Toda ruta salvo `/salud` exige token (`401 NO_AUTENTICADO`; `503 SERVICIO_NO_DISPONIBLE` si Keycloak no entrega el JWKS). Solo `admin`, `gerente` y `residente`; los demás roles reciben `403 SIN_PERMISO` en todo el servicio. Una ruta que no existe: `404 RUTA_NO_ENCONTRADA` (con token válido).
- El cliente REST del gateway hacia este servicio (`IA_URL`) se agrega en la 402.

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
