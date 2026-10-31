"""Entrena el Isolation Forest de consumos atípicos (403, diseño «Entrenamiento» y «Métricas mínimas»).

    uv run python -m ml.atipicos.entrenar --fuente sintetica --version ca-1.0.0
    uv run python -m ml.atipicos.entrenar --fuente real --version ca-1.1.0   # requiere ≥ 500 salidas en rpt_movimiento

- `sintetica`: lee `ml/atipicos/datos/salidas_sinteticas.csv` (de `generar.py`), divide por obra 80 % / 20 % (el
  entrenamiento incluye las anomalías, como pasaría con datos reales) y mide sobre el 20 % con la marca `anomalia`.
- `real`: lee las salidas de `rpt_movimiento` (`IA_REPORTES_DB_URL`; incluye las de la carga inicial), calcula las
  variables en orden de fecha y reporta la proporción marcada y, si ya hay revisiones en `ia_db`, la proporción de
  `CONFIRMADO` entre las revisadas (precisión observada, solo como referencia).

Si se cumplen las métricas mínimas, escribe `modelos/consumos_atipicos/<version>/` (`modelo.joblib`,
`percentiles.json`, `metricas.json`, `parametros.yaml`) y cambia `actual.txt`. Si no, termina con error (código 1)
sin escribir nada.
"""

import argparse
import csv
import json
import shutil
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import yaml
from sklearn.ensemble import IsolationForest
from sklearn.metrics import roc_auc_score

from ia.atipicos.dominio.historial import recorrer_en_orden
from ia.atipicos.dominio.motivos import Percentiles
from ia.atipicos.dominio.valores import CONFIRMADO, MAXIMA_PROPORCION_REAL, MINIMO_SALIDAS_REALES, NORMAL
from ia.atipicos.dominio.variables import NOMBRES_VARIABLES, SalidaMaterial
from ia.atipicos.infraestructura.modelo import (
    ARCHIVO_ACTUAL,
    ARCHIVO_METRICAS,
    ARCHIVO_MODELO,
    ARCHIVO_PARAMETROS,
    ARCHIVO_PERCENTILES,
    PATRON_VERSION,
    RAIZ_MODELOS,
)
from ia.atipicos.infraestructura.repositorio_mongo import contar_revisiones
from ia.atipicos.infraestructura.salidas_sql import leer_salidas
from ia.comun.base_datos import crear_motor
from ia.comun.bucle import ejecutar_async
from ia.comun.mongo import crear_cliente_mongo
from ia.comun.tiempo import iso_utc
from ia.config import Configuracion
from ml.atipicos.generar import PARAMETROS, SALIDA_CSV

SEMILLA = 2026
# `max_samples` = 4096 (decisión de Jairo, 31/10/2026; el de scikit-learn es 256): con muestras de 256, las salidas
# de noche, en domingo o en ráfaga casi nunca aparecen en la muestra de un árbol y el bosque no las aísla.
PARAMETROS_MODELO: dict[str, Any] = {
    "n_estimators": 200,
    "contamination": 0.02,
    "max_samples": 4096,
    "random_state": SEMILLA,
}
PROPORCION_PRUEBA = 0.2

# Métricas mínimas sobre el 20 % de prueba sintético (decisión del equipo).
MINIMA_SENSIBILIDAD = 0.70
MINIMA_PRECISION = 0.50
MINIMO_AUC = 0.85
PROPORCION_MARCADA = (0.01, 0.03)


class EntrenamientoFallido(Exception):
    """Datos insuficientes o métricas por debajo del mínimo: no se escribe nada."""


@dataclass
class Resultado:
    modelo: Any
    percentiles: Percentiles
    metricas: dict[str, Any]
    parametros: dict[str, Any] = field(default_factory=dict)
    """Lo que se copia como `parametros.yaml` de la versión."""


# Datos.


def leer_csv(ruta: Path) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    """Variables, marca `anomalia`, obra y tipo de anomalía de cada fila del CSV sintético."""
    if not ruta.is_file():
        raise EntrenamientoFallido(f"No existe {ruta}: genera los datos con «python -m ml.atipicos.generar».")
    with ruta.open(encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    if not filas:
        raise EntrenamientoFallido(f"{ruta} no tiene filas.")
    x = np.array([[float(fila[nombre]) for nombre in NOMBRES_VARIABLES] for fila in filas], dtype=float)
    y = np.array([int(fila["anomalia"]) for fila in filas], dtype=int)
    return x, y, [fila["obra_id"] for fila in filas], [fila.get("tipo_anomalia") or "" for fila in filas]


def variables_de(salidas: Sequence[SalidaMaterial]) -> np.ndarray:
    """Variables de cada salida con su historial anterior, en orden de fecha (las mismas que en el worker)."""
    return np.array([variables.vector() for _, variables in recorrer_en_orden(salidas)], dtype=float)


def dividir_por_obra(obras: Sequence[str], semilla: int = SEMILLA) -> np.ndarray:
    """Máscara de prueba: el 20 % de las obras (al azar con la semilla); sus salidas no se usan para entrenar."""
    distintas = sorted(set(obras))
    n_prueba = max(1, round(len(distintas) * PROPORCION_PRUEBA))
    prueba = set(np.random.default_rng(semilla).permutation(distintas)[:n_prueba].tolist())
    return np.array([obra in prueba for obra in obras], dtype=bool)


def calcular_percentiles(x: np.ndarray) -> Percentiles:
    p1 = np.percentile(x, 1, axis=0)
    p99 = np.percentile(x, 99, axis=0)
    return Percentiles(
        p1={nombre: float(p1[i]) for i, nombre in enumerate(NOMBRES_VARIABLES)},
        p99={nombre: float(p99[i]) for i, nombre in enumerate(NOMBRES_VARIABLES)},
    )


def nuevo_modelo() -> IsolationForest:
    return IsolationForest(**PARAMETROS_MODELO)


# Entrenamientos.


def entrenar_sintetico(
    x: np.ndarray, y: np.ndarray, obras: Sequence[str], tipos: Sequence[str] | None = None
) -> Resultado:
    prueba = dividir_por_obra(obras)
    if prueba.all() or not prueba.any():
        raise EntrenamientoFallido("Hacen falta al menos 2 obras para dividir 80 % / 20 %.")
    x_entrena, x_prueba, y_prueba = x[~prueba], x[prueba], y[prueba]
    modelo = nuevo_modelo().fit(x_entrena)
    marcadas = modelo.predict(x_prueba) == -1
    puntaje = -modelo.score_samples(x_prueba)
    anomalas = y_prueba == 1
    aciertos = int((marcadas & anomalas).sum())
    metricas: dict[str, Any] = {
        "filas_entrenamiento": len(x_entrena),
        "filas_prueba": len(x_prueba),
        "anomalias_prueba": int(anomalas.sum()),
        "marcadas_prueba": int(marcadas.sum()),
        "sensibilidad": aciertos / int(anomalas.sum()) if anomalas.any() else 0.0,
        "precision": aciertos / int(marcadas.sum()) if marcadas.any() else 0.0,
        "auc_roc": float(roc_auc_score(y_prueba, puntaje)) if 0 < anomalas.sum() < len(y_prueba) else 0.0,
        "proporcion_marcada": float(marcadas.mean()),
    }
    if tipos is not None:
        tipos_prueba = np.array(tipos)[prueba]
        metricas["sensibilidad_por_tipo"] = {
            tipo: float(marcadas[tipos_prueba == tipo].mean()) for tipo in sorted(set(tipos_prueba) - {""})
        }
    metricas["minimos"] = {
        "sensibilidad": MINIMA_SENSIBILIDAD,
        "precision": MINIMA_PRECISION,
        "auc_roc": MINIMO_AUC,
        "proporcion_marcada": list(PROPORCION_MARCADA),
    }
    metricas["fallas"] = [
        texto
        for cumple, texto in (
            (metricas["sensibilidad"] >= MINIMA_SENSIBILIDAD, f"sensibilidad < {MINIMA_SENSIBILIDAD}"),
            (metricas["precision"] >= MINIMA_PRECISION, f"precisión < {MINIMA_PRECISION}"),
            (metricas["auc_roc"] >= MINIMO_AUC, f"AUC-ROC < {MINIMO_AUC}"),
            (
                PROPORCION_MARCADA[0] <= metricas["proporcion_marcada"] <= PROPORCION_MARCADA[1],
                "proporción marcada fuera de 1 % a 3 %",
            ),
        )
        if not cumple
    ]
    return Resultado(modelo=modelo, percentiles=calcular_percentiles(x_entrena), metricas=metricas)


def entrenar_real(x: np.ndarray, revisiones: dict[str, int] | None) -> Resultado:
    if len(x) < MINIMO_SALIDAS_REALES:
        raise EntrenamientoFallido(
            f"Hay {len(x)} salidas en rpt_movimiento; hacen falta al menos {MINIMO_SALIDAS_REALES} para reentrenar."
        )
    modelo = nuevo_modelo().fit(x)
    marcadas = modelo.predict(x) == -1
    metricas: dict[str, Any] = {
        "filas_entrenamiento": len(x),
        "marcadas": int(marcadas.sum()),
        "proporcion_marcada": float(marcadas.mean()),
        "revisiones": revisiones,
        "precision_observada": None,
        "minimos": {"proporcion_marcada_maxima": MAXIMA_PROPORCION_REAL},
    }
    if revisiones and (revisadas := revisiones.get(NORMAL, 0) + revisiones.get(CONFIRMADO, 0)):
        metricas["precision_observada"] = revisiones.get(CONFIRMADO, 0) / revisadas
    metricas["fallas"] = (
        [] if metricas["proporcion_marcada"] <= MAXIMA_PROPORCION_REAL else ["proporción marcada > 3 %"]
    )
    return Resultado(modelo=modelo, percentiles=calcular_percentiles(x), metricas=metricas)


# Artefactos.


def guardar(resultado: Resultado, version: str, raiz: Path, fuente: str, segundos: float) -> Path:
    """Escribe la versión y cambia `actual.txt` (solo si se cumplieron las métricas)."""
    carpeta = raiz / version
    temporal = raiz / f".{version}.tmp"
    if temporal.exists():
        shutil.rmtree(temporal)
    temporal.mkdir(parents=True)
    joblib.dump(resultado.modelo, temporal / ARCHIVO_MODELO, compress=3)
    (temporal / ARCHIVO_PERCENTILES).write_text(
        json.dumps(resultado.percentiles.como_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    metricas = {
        "version": version,
        "fuente": fuente,
        "entrenado_en": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "segundos": round(segundos, 2),
        "variables": list(NOMBRES_VARIABLES),
        "modelo": {"tipo": "IsolationForest", **PARAMETROS_MODELO},
        **resultado.metricas,
    }
    (temporal / ARCHIVO_METRICAS).write_text(
        json.dumps(metricas, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (temporal / ARCHIVO_PARAMETROS).write_text(
        yaml.safe_dump(resultado.parametros, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    if carpeta.exists():
        shutil.rmtree(carpeta)
    temporal.rename(carpeta)
    (raiz / ARCHIVO_ACTUAL).write_text(version + "\n", encoding="utf-8")
    return carpeta


async def entrenar_con_reales(config: Configuracion) -> Resultado:
    """Salidas de `rpt_movimiento` (≥ 500) y revisiones de `ia_db` (si MongoDB no responde, se entrena igual)."""
    motor = crear_motor(config.reportes_db_url)
    try:
        salidas = await leer_salidas(motor)
    finally:
        await motor.dispose()
    if len(salidas) < MINIMO_SALIDAS_REALES:
        raise EntrenamientoFallido(
            f"Hay {len(salidas)} salidas en rpt_movimiento; hacen falta al menos {MINIMO_SALIDAS_REALES} para "
            "reentrenar."
        )
    mongo = crear_cliente_mongo(config.mongo_url)
    try:
        revisiones: dict[str, int] | None = await contar_revisiones(mongo[config.mongo_db])
    except Exception as error:  # MongoDB es solo una referencia (precisión observada).
        print(f"Sin revisiones de ia_db ({type(error).__name__}); se entrena igual.", file=sys.stderr)
        revisiones = None
    finally:
        await mongo.close()
    resultado = entrenar_real(variables_de(salidas), revisiones)
    resultado.parametros = {
        "fuente": "real",
        "salidas": len(salidas),
        "desde": iso_utc(salidas[0].registrado_en),
        "hasta": iso_utc(salidas[-1].registrado_en),
    }
    return resultado


def entrenar_con_sinteticos(datos: Path, parametros: Path) -> Resultado:
    x, y, obras, tipos = leer_csv(datos)
    resultado = entrenar_sintetico(x, y, obras, tipos)
    resultado.parametros = {
        "fuente": "sintetica",
        "datos": datos.name,
        "generador": yaml.safe_load(parametros.read_text(encoding="utf-8")),
    }
    return resultado


def terminar(resultado: Resultado, version: str, raiz: Path, fuente: str, segundos: float) -> int:
    """Muestra las métricas; si se cumplen, guarda la versión y cambia `actual.txt` (0); si no, 1 sin escribir."""
    print(json.dumps(resultado.metricas, indent=2, ensure_ascii=False))
    if resultado.metricas["fallas"]:
        print(
            f"No cumple las métricas mínimas ({'; '.join(resultado.metricas['fallas'])}): no se guarda {version} y "
            f"{ARCHIVO_ACTUAL} no cambia.",
            file=sys.stderr,
        )
        return 1
    carpeta = guardar(resultado, version, raiz, fuente, segundos)
    print(f"Modelo {version} en {carpeta} ({segundos:.1f} s); {ARCHIVO_ACTUAL} = {version}.")
    return 0


def main(argumentos: Sequence[str] | None = None) -> int:
    lector = argparse.ArgumentParser(description="Entrena el Isolation Forest de consumos atípicos (403).")
    lector.add_argument("--fuente", choices=("sintetica", "real"), required=True)
    lector.add_argument("--version", required=True, help="ca-X.Y.Z")
    lector.add_argument("--datos", type=Path, default=SALIDA_CSV, help="CSV sintético (solo con --fuente sintetica)")
    lector.add_argument("--parametros", type=Path, default=PARAMETROS, help="parámetros del generador sintético")
    lector.add_argument("--modelos", type=Path, default=RAIZ_MODELOS, help="carpeta de las versiones")
    opciones = lector.parse_args(argumentos)
    if not PATRON_VERSION.match(opciones.version):
        print(f"Versión inválida: {opciones.version} (se espera ca-X.Y.Z).", file=sys.stderr)
        return 2

    inicio = time.perf_counter()
    try:
        if opciones.fuente == "sintetica":
            resultado = entrenar_con_sinteticos(opciones.datos, opciones.parametros)
        else:
            resultado = ejecutar_async(entrenar_con_reales(Configuracion()))
    except EntrenamientoFallido as error:
        print(f"No se entrenó: {error}", file=sys.stderr)
        return 1
    return terminar(resultado, opciones.version, opciones.modelos, opciones.fuente, time.perf_counter() - inicio)


if __name__ == "__main__":
    sys.exit(main())
