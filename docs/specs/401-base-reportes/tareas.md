# 401 · Servicio de IA y base de reportes · tareas de `services/ia`

Requisitos y diseño: `docs/specs/401-base-reportes/` de `supervision-docs` (carpeta raíz del proyecto). El repositorio ya existe (GitHub `supervision-ms-ia`, ramas `main` y `develop`, solo con `README.md` y `.gitignore`); estas tareas lo llenan. La 401 va antes que la 301 (orden de capas 4 → 3 → 5 → 2 → 6).

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] Proyecto: `pyproject.toml` con `uv` y Python 3.12, `.env.example` (incluidas `IA_OBRAS_URL`, `IA_MATERIALES_URL`, `IA_FINANZAS_URL`, `IA_REPORTES_CLIENT_ID` e `IA_REPORTES_CLIENT_SECRET`), `.gitignore`, `Dockerfile` (una imagen, tres comandos: `ia-api`, `ia-worker` e `ia-carga-inicial`)
- [x] `CLAUDE.md` del proyecto y `docs/` (`estructura.md`, `servicio.md`, `datos.md`, `contratos.md`, `specs/README.md`) con lo de esta spec (CQRS: modelo de lectura alimentado por eventos; DDD + hexagonal ligera)
- [x] Esqueleto FastAPI en el puerto 8085: `GET /salud`, Problem Details, `X-Request-Id`, registros JSON/texto; pruebas
- [ ] Validación del token con `PyJWT` (`PyJWKClient`, emisor, audiencia `plataforma-api`), roles y visibilidad por `residente_id`; pruebas con tokens firmados en la prueba
- [ ] Alembic: migración inicial de `reportes_db` (`rpt_obra_resumen`, `rpt_planilla` con `cargada_en`, `rpt_consumo_material`, `rpt_movimiento`, `rpt_cobro`, `evento_aplicado`, `carga_inicial`) y conexión a `ia_db`
- [ ] Dominio de `reportes`: avance, plazo transcurrido y vencimiento con fechas fijas; pruebas
- [ ] Worker: conexión `aio-pika`, cola `ia` y enlaces, inbox en la misma transacción, reintentos con `x-intentos` y `plataforma.fallidos`; pruebas con canal simulado
- [ ] Proyecciones de los 6 eventos como casos de uso compartidos (idempotentes por fila; bloqueo de la fila de la obra en `PlanillaAprobada` y `PlanillaObservada`; `MovimientoRegistrado` suma solo si insertó; orden indistinto de `CobroEmitido` y `PlanillaAprobada`); pruebas con PostgreSQL real
- [ ] Tareas programadas (00:20 cobros vencidos, 00:30 plazo) con `apscheduler`; pruebas de las funciones que llaman
- [ ] Carga inicial (`ia.carga_inicial`): token de `ia-reportes` por *client credentials*, clientes `httpx` de los 7 endpoints con paginación, transacción y bloqueo por obra, `cargada_en`, aproximaciones documentadas, comprobación del avance contra Obras, tabla `carga_inicial` y bloqueo de una sola corrida; pruebas con `httpx.MockTransport` y PostgreSQL real (las de «Pruebas»: datos de la 011, dos corridas, eventos viejos y en paralelo, fallo a mitad)
- [ ] `GET /reportes/estado` (rol `admin`, con `cargaInicial`); pruebas
