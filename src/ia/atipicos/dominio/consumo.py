"""Consumo atípico: una `SALIDA` que el modelo marcó como rara, pendiente de revisión por una persona (403)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ia.atipicos.dominio.valores import PENDIENTE


@dataclass(frozen=True)
class ConsumoAtipico:
    id: str
    """`movimiento_id` de la salida (ref Materiales): una salida se guarda una sola vez."""
    obra_id: str
    material_id: str
    partida_id: str
    cantidad: Decimal
    periodo: str
    registrado_en: datetime
    puntaje: float
    motivos: tuple[str, ...]
    variables: Mapping[str, float]
    version_modelo: str
    detectado_en: datetime
    estado_revision: str = PENDIENTE
    revisado_por: str | None = None
    revisado_en: datetime | None = None
    comentario: str | None = None
