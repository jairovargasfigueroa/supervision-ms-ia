"""Valores del modelo de lectura (los mismos nombres que usan Obras, Materiales y Finanzas)."""

from typing import Final

# rpt_obra_resumen.estado (CONCLUIDA no se usa: el sistema base no tiene esa transición).
SIN_CONTRATO: Final = "SIN_CONTRATO"
EN_EJECUCION: Final = "EN_EJECUCION"

# rpt_planilla.estado
APROBADA: Final = "APROBADA"
OBSERVADA: Final = "OBSERVADA"

# rpt_cobro.estado
PENDIENTE: Final = "PENDIENTE"
VENCIDO: Final = "VENCIDO"

# rpt_movimiento.tipo
INGRESO: Final = "INGRESO"
SALIDA: Final = "SALIDA"
TIPOS_MOVIMIENTO: Final = frozenset({INGRESO, SALIDA})

# Zona de la obra (decisión del equipo, 401); None = sin zona.
ZONAS: Final = frozenset({"NORTE", "SUR", "ESTE", "OESTE", "CENTRO"})

# Estados de la planilla en Obras que la carga inicial solo carga si tienen observación.
ESTADOS_PLANILLA_ABIERTA: Final = frozenset({"BORRADOR", "PRESENTADA", "APROBANDO"})
