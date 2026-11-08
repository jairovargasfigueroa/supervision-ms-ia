"""Valores de los consumos atípicos (403)."""

from typing import Final

# ia_db.consumo_atipico.estado_revision
PENDIENTE: Final = "PENDIENTE"
NORMAL: Final = "NORMAL"
CONFIRMADO: Final = "CONFIRMADO"
ESTADOS_REVISION: Final = frozenset({PENDIENTE, NORMAL, CONFIRMADO})
ESTADOS_REVISADO: Final = frozenset({NORMAL, CONFIRMADO})
"""Los que acepta `PUT /consumos-atipicos/{id}/revision`."""

# Comentario de la revisión (criterio 7): obligatorio al confirmar, opcional en «Es normal».
COMENTARIO_MINIMO: Final = 5
COMENTARIO_MAXIMO: Final = 300

# Lecturas (diseño, «REST del servicio de IA»).
MAXIMO_LISTA: Final = 200
MAXIMO_OBRAS_RESUMEN: Final = 100

# Reentrenar con datos reales (criterio 13) y proporción máxima marcada con datos reales (criterio 14).
MINIMO_SALIDAS_REALES: Final = 500
MAXIMA_PROPORCION_REAL: Final = 0.03
