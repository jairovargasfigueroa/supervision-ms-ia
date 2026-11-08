"""Arma el `ServicioRiesgo` con sus adaptadores (worker y comando de carga inicial)."""

from typing import Any

from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.comun.tiempo import Reloj
from ia.config import Configuracion
from ia.riesgo.aplicacion.puertos import ModeloRiesgo
from ia.riesgo.aplicacion.recalcular import ServicioRiesgo
from ia.riesgo.infraestructura.fuente_sql import ContadorAtipicosMongo, FuenteRiesgoSql
from ia.riesgo.infraestructura.modelo_xgboost import cargar_modelo
from ia.riesgo.infraestructura.predicciones_mongo import AlmacenPrediccionesMongo


def crear_servicio_riesgo(
    config: Configuracion,
    motor: AsyncEngine,
    ia_db: AsyncDatabase[dict[str, Any]],
    reloj: Reloj,
    modelo: ModeloRiesgo | None = None,
) -> ServicioRiesgo:
    """Con `modelo = None` carga el de `IA_MODELOS_DIR` (versión de `actual.txt` o `IA_MODELO_RIESGO_VERSION`)."""
    return ServicioRiesgo(
        FuenteRiesgoSql(motor, config.zona_horaria),
        ContadorAtipicosMongo(ia_db),
        AlmacenPrediccionesMongo(ia_db),
        modelo if modelo is not None else cargar_modelo(config.modelos_dir, config.modelo_riesgo_version),
        reloj,
    )
