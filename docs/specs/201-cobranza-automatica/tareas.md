# 201 · Cobranza automática · tareas de `services/ia`

Requisitos y diseño: `docs/specs/201-cobranza-automatica/` de `supervision-docs` (carpeta raíz del proyecto; la parte de este servicio está en «IA (401, 503)» del diseño).

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] Aplicar «Cambios a contratos y datos» que tocan este proyecto (`docs/datos.md`, `docs/contratos.md`)
- [x] Migración de `reportes_db`: `rpt_cobro` agrega `pagado_en`; el estado admite `PAGADO`
- [x] Consumidor de `evento.finanzas.CobroPagado` (inbox, reintentos y fallidos como los demás): `rpt_cobro.estado = PAGADO` y `pagado_en`
- [ ] La tarea diaria de vencidos no toca los `PAGADO`; la carga inicial (401) lee el estado `PAGADO` y `pagadoEn` de `GET /cobros`
- [ ] Catálogo de reportes (503): en el conjunto de cobros, el valor `PAGADO` del filtro de estado y la columna «Fecha de pago»; pruebas del constructor y de la seguridad (504) con el valor nuevo
