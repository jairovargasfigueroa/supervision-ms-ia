# Modelo de riesgo de retraso (402)

Clasificador XGBoost que estima la probabilidad de que una obra termine **después de `plazo_dias × 1,10`**. Se entrena en CPU (`tree_method = "hist"`), sin GPU ni modelo de lenguaje. Diseño completo: `docs/specs/402-riesgo-retraso/diseno.md` de la carpeta general.

## Comandos

```sh
uv run python -m ml.riesgo.generar --obras 4000 --semilla 2026                       # datos sintéticos (ml/riesgo/datos/, no se versiona)
uv run python -m ml.riesgo.entrenar --fuente sintetica --version rr-1.0.0            # entrena y publica si cumple
uv run python -m ml.riesgo.entrenar --fuente real --archivo <ruta.csv> --version rr-1.1.0   # reentrenar con datos reales
```

- `entrenar` escribe `modelos/riesgo_retraso/<version>/` (`modelo.ubj`, `variables.json`, `metricas.json` y, con datos sintéticos, `parametros.yaml`) y cambia `modelos/riesgo_retraso/actual.txt` **solo si** cumple las métricas mínimas; si no, termina con código 1 y no escribe nada. Una versión que ya existe no se sobrescribe.
- El servicio carga la versión de `actual.txt` (o la de `IA_MODELO_RIESGO_VERSION`) al arrancar: después de publicar una versión, reinicia `ia-api` e `ia-worker`.
- En Windows, si la consola muestra mal los acentos, corre con `PYTHONIOENCODING=utf-8`.

## Datos sintéticos

`generar.py` simula obras completas mes a mes con las reglas de `parametros.yaml` (semilla 2026): curva S planificada, eficiencia oculta, lluvia de diciembre a marzo, problemas de material (consumos atípicos y salidas sin stock) y observaciones de planillas (una planilla observada se aprueba al mes siguiente, junto con el avance de ese mes, y no se vuelve a observar); sigue después del plazo hasta llegar a 100 % para conocer la etiqueta. En 3 días al azar por obra (5 % a 95 % del plazo) arma el estado de la obra y llama a `calcular_variables` (`src/ia/riesgo/dominio/variables.py`), la misma función que usa el servicio.

**Ajuste documentado (31/10/2026):** con `ritmo_despues_del_plazo: 1.0` se retrasaba el 52 % de las obras (fuera del objetivo de 25 % a 45 %); con `2.0` (una obra atrasada concentra recursos al final) se retrasa el 36,8 %.

## Versión publicada: `rr-1.0.0`

Datos sintéticos: 4 000 obras, 12 000 filas (9 600 de entrenamiento y 2 400 de prueba, divididas por obra). Hiperparámetros elegidos por validación cruzada por grupos (5 pliegues, 6 combinaciones): `max_depth = 5`, `learning_rate = 0,05`, 244 árboles.

| Métrica (20 % de prueba) | Modelo | Regla (`brecha`) | Mínimo |
|---|---|---|---|
| AUC-ROC | **0,9033** | 0,7683 | ≥ 0,80 y ≥ regla + 0,05 |
| Sensibilidad con p ≥ 0,30 | **0,8753** | 0,8366 (con `brecha > 10`) | ≥ 0,80 |
| Precisión con p ≥ 0,60 | **0,8306** | 0,5181 (con `brecha > 10`) | ≥ 0,70 |
| Brier | **0,1229** | — | ≤ 0,18 |

**Tiempo medido** en la computadora del equipo (CPU, sin GPU, 31/10/2026, con otros procesos corriendo): generar 1,1 s y entrenar 11,8 s; los dos comandos completos con `uv run`, 20 s (en una corrida anterior, con más carga en la máquina, 35 s). Menos de un minuto.

Estas métricas muestran que el modelo aprende las relaciones simuladas; **no prueban que acierte con obras reales**.

## Formato del CSV real (`--fuente real`)

Las **mismas columnas** que `riesgo_sintetico.csv`, con encabezado, separadas por coma y punto decimal:

| Columna | Qué es |
|---|---|
| `obra_id` | identificador de la obra (los cortes de una misma obra quedan del mismo lado de la división) |
| `fecha_corte` | día del corte (`AAAA-MM-DD`) |
| `plazo_pct`, `avance_pct`, `brecha`, `ritmo_reciente`, `ritmo_requerido`, `relacion_ritmo`, `meses_sin_planilla`, `planillas_observadas`, `atipicos_90d`, `supera_stock_90d`, `plazo_dias`, `log_monto` | las 12 variables, calculadas con `calcular_variables` con lo que se sabía de la obra ese día |
| `se_retrasa` | `1` si la obra terminó después de `plazo_dias × 1,10`; si no, `0` |

Ejemplo:

```csv
obra_id,fecha_corte,plazo_pct,avance_pct,brecha,ritmo_reciente,ritmo_requerido,relacion_ritmo,meses_sin_planilla,planillas_observadas,atipicos_90d,supera_stock_90d,plazo_dias,log_monto,se_retrasa
b0000000-0000-4000-8000-000000000001,2026-10-28,33.33,28.49,4.84,14.245,9.0717,1.5703,2.0,0.0,0.0,0.0,360.0,14.8357,0
b0000000-0000-4000-8000-000000000002,2026-10-28,50.0,16.34,33.66,16.34,16.9213,0.9657,2.0,1.0,0.0,1.0,300.0,14.9741,1
```

- Lo arma el equipo con obras **terminadas** de la constructora, fuera de la plataforma (el sistema base no tiene la transición a `CONCLUIDA`).
- Mínimo **50 obras distintas** (decisión del equipo); con menos, o con columnas que faltan, variables vacías o `se_retrasa` distinto de 0 y 1, el comando termina con error.
- Mismas métricas mínimas; `metricas.json` guarda `"datos": "REALES"` y las filas usadas.

**Cómo lee el modelo la brecha.** En la simulación, una planilla observada retrasa un mes la aprobación de su avance, así que parte de la brecha puede ser «papeleo» y no atraso real: el modelo aprendió a pesar la brecha junto con `meses_sin_planilla` y el ritmo reciente. Por eso puede dar riesgo `BAJO` a una obra que la regla marca «En riesgo» (por ejemplo, la obra 2 de los datos de prueba de la 011: brecha 33,7 pero ritmo reciente casi igual al necesario). Son dos señales distintas (criterio 7).
