"""Modelo de riesgo de retraso en formato nativo de XGBoost (`modelos/riesgo_retraso/<versión>/modelo.ubj`)."""

import json
import logging
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

import numpy as np
import xgboost as xgb

from ia.comun.tiempo import leer_instante
from ia.riesgo.aplicacion.puertos import InfoModelo

registro = logging.getLogger("ia.riesgo.modelo")

CARPETA_RIESGO = "riesgo_retraso"


class ModeloXgboost:
    def __init__(self, booster: xgb.Booster, info: InfoModelo, orden: tuple[str, ...]) -> None:
        self._booster = booster
        self._info = info
        self._orden = orden

    @property
    def info(self) -> InfoModelo:
        return self._info

    def predecir(self, variables: Mapping[str, float]) -> tuple[float, dict[str, float]]:
        fila = np.array([[float(variables[nombre]) for nombre in self._orden]], dtype=np.float32)
        matriz = xgb.DMatrix(fila, feature_names=list(self._orden))
        probabilidad = float(self._booster.predict(matriz)[0])
        contribuciones = self._booster.predict(matriz, pred_contribs=True)[0]
        # La última columna es el sesgo (bias): no es una variable.
        return probabilidad, {nombre: float(contribuciones[i]) for i, nombre in enumerate(self._orden)}


def cargar_modelo(carpeta_modelos: Path | str, version: str | None = None) -> ModeloXgboost | None:
    """Carga la versión indicada (o la de `actual.txt`). Si no hay modelo o está incompleto, registra el error y
    devuelve `None`: las predicciones quedan `SIN_DATOS`."""
    raiz = Path(carpeta_modelos) / CARPETA_RIESGO
    try:
        version = version or (raiz / "actual.txt").read_text(encoding="utf-8").strip()
        carpeta = raiz / version
        metricas = json.loads((carpeta / "metricas.json").read_text(encoding="utf-8"))
        definicion = json.loads((carpeta / "variables.json").read_text(encoding="utf-8"))
        booster = xgb.Booster()
        booster.load_model(str(carpeta / "modelo.ubj"))
    except (OSError, ValueError, KeyError, xgb.core.XGBoostError) as error:
        registro.error("No se pudo cargar el modelo de riesgo de retraso (%s): %s", raiz, error)
        return None
    orden = tuple(v["nombre"] for v in definicion["variables"])
    info = InfoModelo(
        version=version,
        entrenado_en=_instante(metricas["entrenadoEn"]),
        datos=metricas["datos"],
        filas_entrenamiento=int(metricas["filasEntrenamiento"]),
        metricas=metricas["metricas"],
        umbrales=metricas["umbrales"],
        variables=tuple((v["nombre"], v["descripcion"]) for v in definicion["variables"]),
    )
    registro.info("Modelo de riesgo de retraso %s cargado (%s).", version, info.datos)
    return ModeloXgboost(booster, info, orden)


def _instante(texto: str) -> datetime:
    return leer_instante(texto)
