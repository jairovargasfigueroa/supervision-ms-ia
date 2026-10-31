"""Datos ya traducidos que reciben los casos de uso, vengan de un evento o de la carga inicial."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class ObraLeida:
    obra_id: str
    nombre: str
    ubicacion: str | None
    zona: str | None
    cliente_id: str | None
    residente_id: str | None
    maestro_id: str | None
    almacenero_id: str | None
    fecha_inicio: date | None
    plazo_dias: int | None
    monto_contrato: Decimal | None


@dataclass(frozen=True)
class PlanillaAprobadaLeida:
    planilla_id: str
    obra_id: str
    periodo: str
    neto: Decimal
    cobro_id: str | None
    montos_lineas: tuple[Decimal, ...]
    aprobada_en: datetime | None
    """`ocurridoEn` de `PlanillaAprobada`; null si la trajo la carga inicial."""


@dataclass(frozen=True)
class PlanillaObservadaLeida:
    planilla_id: str
    obra_id: str
    motivo: str | None
    origen: str | None
    ocurrida_en: datetime | None


@dataclass(frozen=True)
class CobroEmitidoLeido:
    cobro_id: str
    planilla_id: str
    numero: str
    vence_en: date
    emitido_en: date | None
    """Fecha de `ocurridoEn` en La Paz."""


@dataclass(frozen=True)
class MovimientoLeido:
    movimiento_id: str
    obra_id: str
    material_id: str
    tipo: str
    cantidad: Decimal
    partida_id: str | None
    periodo: str
    supera_stock: bool
    registrado_en: datetime
    precio_referencial: Decimal | None
    """Precio del material al registrar el movimiento; null en eventos anteriores al cambio."""


# Carga inicial (lo que devuelven los endpoints REST de lectura).


@dataclass(frozen=True)
class ObraCargada:
    obra: ObraLeida
    estado: str
    """Estado en Obras (`SIN_CONTRATO`, `EN_EJECUCION`…)."""
    avance_pct: Decimal | None
    """Avance que muestra Obras, para comprobar el calculado."""


@dataclass(frozen=True)
class PlanillaResumenCargada:
    planilla_id: str
    estado: str


@dataclass(frozen=True)
class PlanillaCargada:
    planilla_id: str
    obra_id: str
    estado: str
    periodo: str | None
    neto: Decimal | None
    montos_lineas: tuple[Decimal, ...]
    observacion: str | None
    cobro_id: str | None


@dataclass(frozen=True)
class CobroCargado:
    cobro_id: str
    numero: str | None
    obra_id: str
    planilla_id: str | None
    monto_neto: Decimal | None
    emitido_en: date | None
    vence_en: date | None
    estado: str | None
