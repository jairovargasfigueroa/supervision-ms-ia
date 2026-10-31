# 403 · Consumos atípicos · tareas de `services/ia`

Requisitos y diseño: `docs/specs/403-consumos-atipicos/` de `supervision-docs` (carpeta raíz del proyecto). Requiere la 401 terminada (y la 402 para el recálculo del riesgo; si no está, ese paso se omite). Contratos: `docs/contratos.md` de este proyecto.

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] `atipicos/dominio/variables.py`: `calcular_variables_salida(salida, historial)` y reglas de motivos con percentiles; pruebas con casos fijos
- [x] Generador `ml/atipicos/generar.py` con `parametros.yaml`, semilla 2026, los 4 tipos de anomalía e historial por obra y material en memoria; pruebas de reproducibilidad
- [ ] `ml/atipicos/entrenar.py` (`--fuente sintetica` y `--fuente real`), métricas mínimas y artefactos en `modelos/consumos_atipicos/ca-1.0.0/`; entrenar y versionar `ca-1.0.0` y anotar en el README cuánto tardó (menos de un minuto, en CPU)
  - **Bloqueada en «entrenar y versionar `ca-1.0.0`» (31/10/2026):** el script, los artefactos y las pruebas están hechos, pero con las variables, el generador y `IsolationForest(n_estimators=200, contamination=0.02, random_state=2026)` del diseño la sensibilidad sobre el 20 % de prueba es 0,53 (mínimo 0,70; con las semillas 1 a 4, de 0,48 a 0,59). Precisión 0,53, AUC-ROC 0,94 y 2,3 % marcadas sí cumplen. Por tipo: CANTIDAD 0,96, STOCK 0,93, HORARIO 0,23 y RAFAGA 0,19. Falta que Jairo decida el cambio de la spec (ver el informe de la 403); hasta entonces no hay `actual.txt` y el worker funciona sin detectar.
- [ ] Detección en el worker al aplicar `MovimientoRegistrado` (orden de pasos, historial sin la propia salida aunque la haya traído la carga inicial, idempotencia por `_id`, sin modelo sigue); colección `consumo_atipico` con su índice; pruebas
- [ ] Endpoints `GET /consumos-atipicos`, `GET /consumos-atipicos/resumen` y `PUT /consumos-atipicos/{id}/revision` con visibilidad y errores; pruebas
- [ ] Recálculo del riesgo de retraso de la obra tras un consumo atípico (402, si está implementada); prueba
