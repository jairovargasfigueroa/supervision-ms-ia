# 403 · Consumos atípicos · tareas de `services/ia`

Requisitos y diseño: `docs/specs/403-consumos-atipicos/` de `supervision-docs` (carpeta raíz del proyecto). Requiere la 401 terminada (y la 402 para el recálculo del riesgo; si no está, ese paso se omite). Contratos: `docs/contratos.md` de este proyecto.

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] `atipicos/dominio/variables.py`: `calcular_variables_salida(salida, historial)` y reglas de motivos con percentiles; pruebas con casos fijos
- [x] Generador `ml/atipicos/generar.py` con `parametros.yaml`, semilla 2026, los 4 tipos de anomalía e historial por obra y material en memoria; pruebas de reproducibilidad
- [x] `ml/atipicos/entrenar.py` (`--fuente sintetica` y `--fuente real`), métricas mínimas y artefactos en `modelos/consumos_atipicos/ca-1.0.0/`; entrenar y versionar `ca-1.0.0` y anotar en el README cuánto tardó (menos de un minuto, en CPU)
  - Decisión de Jairo (31/10/2026): con las variables y el modelo del diseño la sensibilidad era 0,53. Se agregaron `salidas_1h` y `salidas_3h` (ráfagas) y `max_samples=4096`; `ca-1.0.0` cumple (sensibilidad 0,72, precisión 0,80, AUC-ROC 0,97, 2,1 % marcadas). Detalle en el README.
- [x] Detección en el worker al aplicar `MovimientoRegistrado` (orden de pasos, historial sin la propia salida aunque la haya traído la carga inicial, idempotencia por `_id`, sin modelo sigue); colección `consumo_atipico` con su índice; pruebas
- [x] Endpoints `GET /consumos-atipicos`, `GET /consumos-atipicos/resumen` y `PUT /consumos-atipicos/{id}/revision` con visibilidad y errores; pruebas
- [x] Recálculo del riesgo de retraso de la obra tras un consumo atípico (402, si está implementada); prueba
