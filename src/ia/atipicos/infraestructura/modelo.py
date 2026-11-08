"""Modelo de consumos atípicos guardado en `modelos/consumos_atipicos/<version>/` (403, diseño «Entrenamiento»).

`modelo.joblib` usa `pickle` por dentro: el servicio **solo** carga archivos de su propio repositorio (esta carpeta, o
la que se le pase en las pruebas), nunca archivos subidos o recibidos por la red.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from ia.atipicos.aplicacion.puertos import Evaluacion
from ia.atipicos.dominio.motivos import Percentiles, motivos_consumo
from ia.atipicos.dominio.variables import NOMBRES_VARIABLES, VariablesSalida

registro = logging.getLogger("ia.atipicos")

RAIZ_MODELOS = Path(__file__).resolve().parents[4] / "modelos" / "consumos_atipicos"
"""`modelos/consumos_atipicos/` del repositorio (también en la imagen: `/app/modelos/...`)."""
ARCHIVO_ACTUAL = "actual.txt"
ARCHIVO_MODELO = "modelo.joblib"
ARCHIVO_PERCENTILES = "percentiles.json"
ARCHIVO_METRICAS = "metricas.json"
ARCHIVO_PARAMETROS = "parametros.yaml"
PATRON_VERSION = re.compile(r"^ca-\d+\.\d+\.\d+$")


class ModeloNoDisponible(Exception):
    """No hay versión elegida, falta un archivo o no se pudo leer."""


@dataclass(frozen=True)
class ModeloAtipicosSklearn:
    modelo: Any
    """`sklearn.ensemble.IsolationForest` ya entrenado."""
    percentiles: Percentiles
    version_modelo: str

    @property
    def version(self) -> str:
        return self.version_modelo

    def evaluar(self, variables: VariablesSalida) -> Evaluacion:
        fila = np.array([variables.vector()], dtype=float)
        puntaje = float(-self.modelo.score_samples(fila)[0])
        atipica = bool(self.modelo.predict(fila)[0] == -1)
        return Evaluacion(
            puntaje=round(min(max(puntaje, 0.0), 1.0), 4),
            atipica=atipica,
            motivos=motivos_consumo(variables, self.percentiles) if atipica else [],
            version=self.version_modelo,
        )


def version_actual(raiz: Path = RAIZ_MODELOS) -> str | None:
    """La versión que indica `actual.txt` (o `None` si no existe)."""
    ruta = raiz / ARCHIVO_ACTUAL
    if not ruta.is_file():
        return None
    texto = ruta.read_text(encoding="utf-8").strip()
    return texto or None


def cargar_modelo(version: str | None = None, raiz: Path = RAIZ_MODELOS) -> ModeloAtipicosSklearn:
    """Carga `version` (`IA_MODELO_ATIPICOS_VERSION`) o la de `actual.txt`. Lanza `ModeloNoDisponible`."""
    elegida = version or version_actual(raiz)
    if not elegida:
        raise ModeloNoDisponible(f"No hay {ARCHIVO_ACTUAL} en {raiz} ni IA_MODELO_ATIPICOS_VERSION.")
    if not PATRON_VERSION.match(elegida):
        raise ModeloNoDisponible(f"Versión de modelo inválida: {elegida!r}.")
    carpeta = raiz / elegida
    try:
        modelo = joblib.load(carpeta / ARCHIVO_MODELO)
        percentiles = Percentiles.desde_dict(json.loads((carpeta / ARCHIVO_PERCENTILES).read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError) as error:
        raise ModeloNoDisponible(f"No se pudo cargar el modelo {elegida}: {error}") from error
    if getattr(modelo, "n_features_in_", None) != len(NOMBRES_VARIABLES):
        raise ModeloNoDisponible(f"El modelo {elegida} no tiene las {len(NOMBRES_VARIABLES)} variables esperadas.")
    return ModeloAtipicosSklearn(modelo=modelo, percentiles=percentiles, version_modelo=elegida)


def cargar_modelo_o_nada(version: str | None = None, raiz: Path = RAIZ_MODELOS) -> ModeloAtipicosSklearn | None:
    """Como `cargar_modelo`, pero sin modelo registra el error y devuelve `None` (el worker sigue sin detectar)."""
    try:
        modelo = cargar_modelo(version, raiz)
    except ModeloNoDisponible as error:
        registro.error("Consumos atípicos sin modelo: %s La detección queda apagada.", error)
        return None
    registro.info("Modelo de consumos atípicos %s cargado.", modelo.version)
    return modelo
