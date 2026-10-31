# 402 · Riesgo de retraso · tareas de `services/ia`

Requisitos y diseño: `docs/specs/402-riesgo-retraso/` de `supervision-docs` (carpeta raíz del proyecto). Requiere la 401 terminada (la 403 no hace falta: sin ella, `atipicos_90d` vale 0). Contratos: `docs/contratos.md` de este proyecto.

Marca cada tarea al terminarla (con sus pruebas y su commit).

- [x] Agregar `xgboost` 3, `scikit-learn` 1, `pandas` 3, `numpy` 2 y `pyyaml` 6 al `pyproject.toml`
- [x] `riesgo/dominio/variables.py`: `calcular_variables(estado_obra, hoy)`, textos de factores y niveles; pruebas con casos fijos
- [x] Generador `ml/riesgo/generar.py` con `parametros.yaml` y semilla 2026; pruebas de reproducibilidad y proporción de retrasadas
- [x] `ml/riesgo/entrenar.py` (`--fuente sintetica` y `--fuente real --archivo`): división por obra, búsqueda de hiperparámetros en CPU (`tree_method = "hist"`), línea base de la regla, métricas mínimas y artefactos en `modelos/riesgo_retraso/rr-1.0.0/`; formato del CSV real en `ml/riesgo/README.md`; entrenar y versionar `rr-1.0.0` y anotar en el README cuánto tardó (menos de un minuto)
- [x] Carga del modelo y predicción con factores (`pred_contribs`); colección `prediccion` con índice único por `clave`; pruebas
- [x] Disparadores en el worker (tras `PlanillaAprobada`, `PlanillaObservada`, `ContratoRegistrado`), predicción de todas las obras al final de la carga inicial (`CARGA_INICIAL`) y tarea diaria de la 01:00; actualización de `rpt_obra_resumen`; pruebas
- [x] Endpoints `GET /predicciones`, `GET /obras/{obraId}/prediccion` (con `SIN_DATOS` para obras visibles sin predicción) y `GET /modelos/riesgo-retraso` (`datos` `SINTETICOS` o `REALES`) con visibilidad y errores; pruebas
- [x] Notebook `ml/riesgo/exploracion.ipynb` con la curva ROC y la importancia de variables para el documento
