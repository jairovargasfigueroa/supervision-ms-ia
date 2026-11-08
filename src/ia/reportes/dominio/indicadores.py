"""Avance, plazo transcurrido y vencimiento de cobros (docs/reglas-negocio.md, «Avance y plazo» y saga, paso 4).

Mismas reglas que Obras (avance y plazo) y Finanzas (vencimiento). Todo con `Decimal` y redondeo final a 2 decimales
«mitad hacia arriba» (convenciones, «Nombres»).
"""

from collections.abc import Iterable
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ia.reportes.dominio.valores import PENDIENTE, VENCIDO

CIEN = Decimal(100)
_DOS_DECIMALES = Decimal("0.01")
_TRES_DECIMALES = Decimal("0.001")


def redondear(valor: Decimal) -> Decimal:
    """2 decimales, mitad hacia arriba (montos y porcentajes)."""
    return valor.quantize(_DOS_DECIMALES, rounding=ROUND_HALF_UP)


def redondear_cantidad(valor: Decimal) -> Decimal:
    """3 decimales, mitad hacia arriba (cantidades de material)."""
    return valor.quantize(_TRES_DECIMALES, rounding=ROUND_HALF_UP)


def porcentaje(parte: Decimal, total: Decimal) -> Decimal:
    """parte / total × 100 con 2 decimales; 0 si el total es 0."""
    if total == 0:
        return redondear(Decimal(0))
    return redondear(parte * CIEN / total)


def monto_periodo(montos_lineas: Iterable[Decimal]) -> Decimal:
    """Monto del periodo de una planilla = Σ montos de sus líneas."""
    return redondear(sum(montos_lineas, Decimal(0)))


def avance_periodo_pct(monto_del_periodo: Decimal, monto_contrato: Decimal) -> Decimal:
    """Avance del periodo (%) = monto del periodo / monto del contrato × 100."""
    return porcentaje(monto_del_periodo, monto_contrato)


def avance_pct(montos_periodo_aprobadas: Iterable[Decimal], monto_contrato: Decimal) -> Decimal:
    """Avance de la obra (%) = Σ monto del periodo de sus planillas APROBADA / monto del contrato × 100.

    Equivale a la regla de Obras (Σ ejecutada acumulada × precio unitario / monto): el monto de cada línea es
    cantidad del periodo × precio unitario. Se recalcula siempre desde todas las planillas, nunca se suma.
    """
    return porcentaje(sum(montos_periodo_aprobadas, Decimal(0)), monto_contrato)


def plazo_pct(fecha_inicio: date, plazo_dias: int, hoy: date) -> Decimal:
    """Plazo transcurrido (%) = días desde la fecha de inicio hasta hoy / plazo en días × 100, entre 0 y 100.

    Antes de iniciar es 0; desde el último día del plazo en adelante, 100 (igual que Obras).
    """
    if plazo_dias <= 0:
        return redondear(Decimal(0))
    dias = Decimal((hoy - fecha_inicio).days)
    return min(max(porcentaje(dias, Decimal(plazo_dias)), redondear(Decimal(0))), redondear(CIEN))


def estado_cobro(vence_en: date | None, hoy: date) -> str:
    """Un cobro PENDIENTE pasa a VENCIDO al día siguiente de su vencimiento (vence hoy: todavía PENDIENTE)."""
    if vence_en is not None and vence_en < hoy:
        return VENCIDO
    return PENDIENTE


def monto_consumo(cantidad: Decimal, precio_referencial: Decimal | None) -> Decimal:
    """Monto de una salida = cantidad × precio referencial; sin precio (eventos anteriores al cambio), 0."""
    if precio_referencial is None:
        return redondear(Decimal(0))
    return redondear(cantidad * precio_referencial)
