"""Entrenamiento del modelo de riesgo de retraso (402): XGBoost en CPU, división por obra y métricas mínimas.

    uv run python -m ml.riesgo.entrenar --fuente sintetica --version rr-1.0.0
    uv run python -m ml.riesgo.entrenar --fuente real --archivo <ruta.csv> --version rr-1.1.0

Escribe `modelos/riesgo_retraso/<version>/` (`modelo.ubj`, `variables.json`, `metricas.json` y, con datos
sintéticos, `parametros.yaml`) y `actual.txt` **solo si** el modelo cumple las métricas mínimas; si no, termina con
error y no escribe nada.
"""

import argparse
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import GroupKFold, GroupShuffleSplit

from ia.riesgo.dominio.variables import DESCRIPCIONES, PLANTILLAS_FACTOR, UMBRAL_ALTO, UMBRAL_MEDIO, VARIABLES
from ml.riesgo.generar import PARAMETROS, SALIDA

RAIZ = Path(__file__).resolve().parents[2]
MODELOS = RAIZ / "modelos" / "riesgo_retraso"
COLUMNAS = ("obra_id", "fecha_corte", *VARIABLES, "se_retrasa")
SEMILLA = 2026
OBRAS_MINIMAS_REALES = 50
UMBRAL_REGLA = 10.0  # regla actual de Obras: plazo − avance > 10 puntos

# Métricas mínimas sobre el 20 % de prueba (decisión del equipo, diseño de la 402).
MINIMOS: dict[str, float] = {
    "aucRoc": 0.80,
    "mejoraSobreRegla": 0.05,
    "recallMedio": 0.80,
    "precisionAlto": 0.70,
    "brierMaximo": 0.18,
}
REJILLA: dict[str, list[Any]] = {"max_depth": [3, 4, 5], "learning_rate": [0.05, 0.1]}
FIJOS: dict[str, Any] = {
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "tree_method": "hist",
    "device": "cpu",
    "objective": "binary:logistic",
    "eval_metric": "logloss",
}
ARBOLES_MAXIMOS = 600
PARADA_TEMPRANA = 30
PLIEGUES = 5


class DatosInvalidos(Exception):
    """El CSV no tiene el formato de `ml/riesgo/README.md` o no alcanza para entrenar."""


@dataclass
class Resultado:
    modelo: xgb.XGBClassifier
    metricas: dict[str, float]
    regla: dict[str, float]
    hiperparametros: dict[str, Any]
    filas_entrenamiento: int
    filas_prueba: int
    obras: int
    fallas: list[str] = field(default_factory=list)

    @property
    def cumple(self) -> bool:
        return not self.fallas


def leer_datos(ruta: Path, fuente: str) -> pd.DataFrame:
    if not ruta.exists():
        raise DatosInvalidos(f"No existe {ruta}. Con datos sintéticos, corre antes: uv run python -m ml.riesgo.generar")
    datos = pd.read_csv(ruta)
    faltan = [columna for columna in COLUMNAS if columna not in datos.columns]
    if faltan:
        raise DatosInvalidos(f"Al CSV le faltan columnas: {', '.join(faltan)}.")
    if datos[list(VARIABLES)].isna().any().any():
        raise DatosInvalidos("Hay variables vacías en el CSV.")
    if not set(datos["se_retrasa"].unique()) <= {0, 1}:
        raise DatosInvalidos("se_retrasa debe valer 0 o 1.")
    obras = datos["obra_id"].nunique()
    if fuente == "REALES" and obras < OBRAS_MINIMAS_REALES:
        raise DatosInvalidos(f"Hay {obras} obras distintas; se necesitan al menos {OBRAS_MINIMAS_REALES}.")
    if datos["se_retrasa"].nunique() < 2:
        raise DatosInvalidos("El CSV necesita obras retrasadas y obras a tiempo.")
    return datos


def entrenar(
    datos: pd.DataFrame,
    semilla: int = SEMILLA,
    minimos: dict[str, float] | None = None,
    rejilla: dict[str, list[Any]] | None = None,
) -> Resultado:
    minimos = minimos or MINIMOS
    rejilla = rejilla or REJILLA
    x = datos[list(VARIABLES)].astype(float)
    y = datos["se_retrasa"].astype(int)
    grupos = datos["obra_id"].astype(str)
    entrenamiento, prueba = next(GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=semilla).split(x, y, grupos))
    x_ent, y_ent, g_ent = x.iloc[entrenamiento], y.iloc[entrenamiento], grupos.iloc[entrenamiento]

    mejor: tuple[float, dict[str, Any], int] | None = None
    for profundidad in rejilla["max_depth"]:
        for tasa in rejilla["learning_rate"]:
            perdidas, iteraciones = [], []
            for ajuste, validacion in GroupKFold(n_splits=PLIEGUES).split(x_ent, y_ent, g_ent):
                modelo = xgb.XGBClassifier(
                    n_estimators=ARBOLES_MAXIMOS,
                    max_depth=profundidad,
                    learning_rate=tasa,
                    early_stopping_rounds=PARADA_TEMPRANA,
                    random_state=semilla,
                    **FIJOS,
                )
                modelo.fit(
                    x_ent.iloc[ajuste],
                    y_ent.iloc[ajuste],
                    eval_set=[(x_ent.iloc[validacion], y_ent.iloc[validacion])],
                    verbose=False,
                )
                perdidas.append(float(modelo.best_score))
                iteraciones.append(int(modelo.best_iteration) + 1)
            perdida = float(np.mean(perdidas))
            if mejor is None or perdida < mejor[0]:
                mejor = (perdida, {"max_depth": profundidad, "learning_rate": tasa}, round(float(np.mean(iteraciones))))
    assert mejor is not None
    _, elegidos, arboles = mejor
    hiperparametros = {**elegidos, "n_estimators": arboles, **FIJOS, "random_state": semilla}
    modelo = xgb.XGBClassifier(**hiperparametros)
    modelo.fit(x_ent, y_ent, verbose=False)

    x_pru, y_pru = x.iloc[prueba], y.iloc[prueba]
    probabilidad = modelo.predict_proba(x_pru)[:, 1]
    brecha = x_pru["brecha"].to_numpy()
    metricas = {
        "aucRoc": _r(roc_auc_score(y_pru, probabilidad)),
        "aucRocRegla": _r(roc_auc_score(y_pru, brecha)),
        "recallMedio": _r(recall_score(y_pru, probabilidad >= UMBRAL_MEDIO, zero_division=0)),
        "precisionAlto": _r(precision_score(y_pru, probabilidad >= UMBRAL_ALTO, zero_division=0)),
        "brier": _r(brier_score_loss(y_pru, probabilidad)),
    }
    regla = {
        "aucRoc": metricas["aucRocRegla"],
        "recall": _r(recall_score(y_pru, brecha > UMBRAL_REGLA, zero_division=0)),
        "precision": _r(precision_score(y_pru, brecha > UMBRAL_REGLA, zero_division=0)),
    }
    fallas = []
    if metricas["aucRoc"] < minimos["aucRoc"]:
        fallas.append(f"AUC-ROC {metricas['aucRoc']} < {minimos['aucRoc']}")
    if metricas["aucRoc"] - metricas["aucRocRegla"] < minimos["mejoraSobreRegla"]:
        fallas.append(
            f"mejora sobre la regla {metricas['aucRoc'] - metricas['aucRocRegla']:.4f} < {minimos['mejoraSobreRegla']}"
        )
    if metricas["recallMedio"] < minimos["recallMedio"]:
        fallas.append(f"sensibilidad con p >= 0,30 {metricas['recallMedio']} < {minimos['recallMedio']}")
    if metricas["precisionAlto"] < minimos["precisionAlto"]:
        fallas.append(f"precisión con p >= 0,60 {metricas['precisionAlto']} < {minimos['precisionAlto']}")
    if metricas["brier"] > minimos["brierMaximo"]:
        fallas.append(f"Brier {metricas['brier']} > {minimos['brierMaximo']}")
    return Resultado(
        modelo=modelo,
        metricas=metricas,
        regla=regla,
        hiperparametros=hiperparametros,
        filas_entrenamiento=len(entrenamiento),
        filas_prueba=len(prueba),
        obras=int(grupos.nunique()),
        fallas=fallas,
    )


def publicar(
    resultado: Resultado,
    version: str,
    datos: str,
    salida: Path,
    semilla: int,
    segundos: float,
    parametros: Path | None,
    minimos: dict[str, float],
) -> Path:
    """Escribe los artefactos de la versión y actualiza `actual.txt`."""
    carpeta = salida / version
    carpeta.mkdir(parents=True)
    resultado.modelo.get_booster().save_model(str(carpeta / "modelo.ubj"))
    variables = [
        {"nombre": nombre, "descripcion": DESCRIPCIONES[nombre], "factor": PLANTILLAS_FACTOR[nombre]}
        for nombre in VARIABLES
    ]
    _json(carpeta / "variables.json", {"variables": variables})
    _json(
        carpeta / "metricas.json",
        {
            "versionModelo": version,
            "entrenadoEn": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "datos": datos,
            "filasEntrenamiento": resultado.filas_entrenamiento,
            "filasPrueba": resultado.filas_prueba,
            "obras": resultado.obras,
            "semilla": semilla,
            "metricas": resultado.metricas,
            "regla": resultado.regla,
            "minimos": minimos,
            "umbrales": {"medio": UMBRAL_MEDIO, "alto": UMBRAL_ALTO},
            "hiperparametros": resultado.hiperparametros,
            "segundosEntrenamiento": round(segundos, 1),
        },
    )
    if parametros is not None:
        shutil.copyfile(parametros, carpeta / "parametros.yaml")
    (salida / "actual.txt").write_text(version + "\n", encoding="utf-8")
    return carpeta


def ejecutar(
    fuente: str,
    version: str,
    archivo: Path | None = None,
    salida: Path = MODELOS,
    semilla: int = SEMILLA,
    minimos: dict[str, float] | None = None,
    parametros: Path = PARAMETROS,
) -> int:
    minimos = minimos or MINIMOS
    if not re.fullmatch(r"rr-\d+\.\d+\.\d+", version):
        print(f"Versión inválida: {version} (formato rr-MAYOR.MENOR.PARCHE).", file=sys.stderr)
        return 1
    if (salida / version).exists():
        print(f"La versión {version} ya existe en {salida}: usa una versión nueva.", file=sys.stderr)
        return 1
    datos_tipo = "SINTETICOS" if fuente == "sintetica" else "REALES"
    ruta = archivo if archivo is not None else SALIDA
    if fuente == "real" and archivo is None:
        print("Con --fuente real hace falta --archivo <ruta.csv>.", file=sys.stderr)
        return 1
    inicio = time.perf_counter()
    try:
        datos = leer_datos(ruta, datos_tipo)
    except DatosInvalidos as error:
        print(f"Datos inválidos: {error}", file=sys.stderr)
        return 1
    resultado = entrenar(datos, semilla=semilla, minimos=minimos)
    segundos = time.perf_counter() - inicio
    print(f"Datos {datos_tipo}: {len(datos)} filas de {resultado.obras} obras; entrenado en {segundos:.1f} s.")
    print(f"Modelo: {json.dumps(resultado.metricas)}")
    print(f"Regla (brecha > 10): {json.dumps(resultado.regla)}")
    if not resultado.cumple:
        print("No cumple las métricas mínimas; no se publica: " + "; ".join(resultado.fallas), file=sys.stderr)
        return 1
    carpeta = publicar(
        resultado,
        version,
        datos_tipo,
        salida,
        semilla,
        segundos,
        parametros if fuente == "sintetica" else None,
        minimos,
    )
    print(f"Publicado {version} en {carpeta} (actual.txt actualizado).")
    return 0


def main(argumentos: list[str] | None = None) -> int:
    lector = argparse.ArgumentParser(description="Entrena el modelo de riesgo de retraso (402).")
    lector.add_argument("--fuente", choices=("sintetica", "real"), required=True)
    lector.add_argument("--version", required=True, help="rr-MAYOR.MENOR.PARCHE")
    lector.add_argument("--archivo", type=Path, help="CSV (obligatorio con --fuente real; ver ml/riesgo/README.md)")
    lector.add_argument("--salida", type=Path, default=MODELOS)
    lector.add_argument("--semilla", type=int, default=SEMILLA)
    args = lector.parse_args(argumentos)
    return ejecutar(args.fuente, args.version, args.archivo, args.salida, args.semilla)


def _r(valor: float) -> float:
    return round(float(valor), 4)


def _json(ruta: Path, contenido: dict[str, Any]) -> None:
    ruta.write_text(json.dumps(contenido, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
