"""Reglas de la revisión de un consumo atípico (403, criterios 7 y 8)."""

from ia.atipicos.dominio.valores import COMENTARIO_MAXIMO, COMENTARIO_MINIMO, CONFIRMADO, ESTADOS_REVISADO

COMENTARIO_OBLIGATORIO = "El comentario es obligatorio al confirmar el problema."
COMENTARIO_LARGO = f"El comentario debe tener entre {COMENTARIO_MINIMO} y {COMENTARIO_MAXIMO} caracteres."


def es_estado_de_revision(estado: str) -> bool:
    """Solo `NORMAL` («Es normal») o `CONFIRMADO` («Confirmar problema»); `PENDIENTE` no es una revisión."""
    return estado in ESTADOS_REVISADO


def normalizar_comentario(comentario: str | None) -> str | None:
    """Sin espacios a los lados; vacío cuenta como sin comentario."""
    if comentario is None:
        return None
    limpio = comentario.strip()
    return limpio or None


def regla_comentario_incumplida(estado: str, comentario: str | None) -> str | None:
    """La regla que no se cumple (texto para `errores[].mensaje`) o `None`.

    Confirmar exige un comentario de 5 a 300 caracteres; «Es normal» lo admite opcional (si viene, con el mismo
    largo).
    """
    if comentario is None:
        return COMENTARIO_OBLIGATORIO if estado == CONFIRMADO else None
    if not COMENTARIO_MINIMO <= len(comentario) <= COMENTARIO_MAXIMO:
        return COMENTARIO_LARGO
    return None
