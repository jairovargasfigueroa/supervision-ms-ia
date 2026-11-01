"""Puertos del riesgo de retraso: el modelo, el almacén de predicciones y lo que se lee de la base de reportes."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from ia.riesgo.dominio.variables import EstadoObra, Factor

# Motivos del cálculo (`ia_db.prediccion.motivo`).
PLANILLA_APROBADA = "PLANILLA_APROBADA"
PLANILLA_OBSERVADA = "PLANILLA_OBSERVADA"
CONTRATO_REGISTRADO = "CONTRATO_REGISTRADO"
CONSUMO_ATIPICO = "CONSUMO_ATIPICO"
CARGA_INICIAL = "CARGA_INICIAL"
DIARIO = "DIARIO"


@dataclass(frozen=True)
class InfoModelo:
    """Lo que muestra `GET /modelos/riesgo-retraso` (sale de `metricas.json` y `variables.json`)."""

    version: str
    entrenado_en: datetime
    datos: str
    """`SINTETICOS` o `REALES`."""
    filas_entrenamiento: int
    metricas: Mapping[str, float]
    umbrales: Mapping[str, float]
    variables: tuple[tuple[str, str], ...]
    """(nombre, descripción) en el orden del modelo."""


class ModeloRiesgo(Protocol):
    @property
    def info(self) -> InfoModelo: ...

    def predecir(self, variables: Mapping[str, float]) -> tuple[float, dict[str, float]]:
        """Probabilidad de «se retrasa» y contribución de cada variable en la escala del modelo (`pred_contribs`)."""
        ...


@dataclass(frozen=True)
class Prediccion:
    clave: str
    """Idempotencia: `{obraId}:{eventoId}`, `{obraId}:diario:{AAAA-MM-DD}` o `{obraId}:carga:{cargaId}`."""
    obra_id: str
    riesgo_retraso: float | None
    nivel: str
    factores: tuple[Factor, ...]
    variables: Mapping[str, float]
    version_modelo: str | None
    motivo: str
    calculada_en: datetime


class AlmacenPredicciones(Protocol):
    async def guardar(self, prediccion: Prediccion) -> bool:
        """Inserta solo si la `clave` no existe. `True` si la insertó."""
        ...

    async def ultimas(self, obra_ids: list[str]) -> dict[str, Prediccion]:
        """La predicción más reciente de cada obra (las que no tienen, no aparecen)."""
        ...

    async def historial(self, obra_id: str, limite: int) -> list[Prediccion]:
        """Las `limite` más recientes con probabilidad (sin `SIN_DATOS`), de la más antigua a la más reciente."""
        ...


@dataclass(frozen=True)
class ObraParaRiesgo:
    """Lo que se lee de la base de reportes para calcular el riesgo de una obra."""

    obra_id: str
    estado: str
    plazo_pct: float | None
    estado_obra: EstadoObra | None
    """`None` si faltan datos del contrato (fecha de inicio, plazo o monto)."""


class FuenteRiesgo(Protocol):
    """Base de reportes (401) y consumos atípicos (403) para el riesgo."""

    async def leer_obra(self, obra_id: str, hoy: date) -> ObraParaRiesgo | None: ...

    async def obras(self, solo_en_ejecucion: bool) -> list[str]: ...

    async def guardar_riesgo(self, obra_id: str, riesgo: float | None, nivel: str, ahora: datetime) -> None:
        """`rpt_obra_resumen.riesgo_retraso`, `nivel_riesgo` y `actualizado_en`."""
        ...


class ContadorAtipicos(Protocol):
    async def contar(self, obra_id: str, desde: datetime) -> int:
        """Consumos atípicos (403) de la obra detectados desde `desde` con estado distinto de `NORMAL`."""
        ...


class VisibilidadObras(Protocol):
    async def visibles(self, obra_ids: list[str], residente_id: str | None) -> set[str]:
        """Las obras de la lista que están en la base de reportes y, con `residente_id`, que son de ese residente."""
        ...


@dataclass(frozen=True)
class ObraResumen:
    obra_id: str
    nombre: str
    estado: str


class ObrasResumen(Protocol):
    """Obras de `rpt_obra_resumen` (401) con su nombre: las lee la herramienta MCP `riesgo_retraso` (501)."""

    async def existentes(self, obra_ids: list[str]) -> dict[str, ObraResumen]: ...

    async def en_ejecucion(self) -> dict[str, ObraResumen]: ...
