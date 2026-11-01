"""`texto_seguro`: limpia un texto que escribió el modelo antes de guardarlo o mostrarlo (504, A8)."""

import re
import unicodedata

_ETIQUETA = re.compile(r"<[^<>]*>")


def texto_seguro(texto: str, maximo: int) -> str:
    """Sin caracteres de control (salvo salto de línea y tabulación), sin etiquetas `<…>`, sin espacios a los lados y
    recortado a `maximo` caracteres."""
    sin_control = "".join(c for c in texto if c in "\n\t" or unicodedata.category(c)[0] != "C")
    anterior = None
    while anterior != sin_control:  # etiquetas anidadas o partidas («<scr<b>ipt>»)
        anterior = sin_control
        sin_control = _ETIQUETA.sub("", sin_control)
    return sin_control.replace("<", "").replace(">", "").strip()[:maximo].strip()
