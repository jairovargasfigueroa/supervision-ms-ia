"""`como_dato`: la **única** forma de pasar datos externos al modelo de lenguaje (504, A3).

Serializa (JSON, o el texto tal cual), escapa `<` y `>` (así un dato no puede cerrar la etiqueta ni abrir otra),
recorta y envuelve en la etiqueta. Las reglas del mensaje de sistema dicen que lo que va dentro es información, no
instrucciones.
"""

import json
import re
from collections.abc import Mapping
from typing import Any

MAXIMO_POR_DEFECTO = 4000
MARCA_RECORTE = "…[recortado]"
_NOMBRE = re.compile(r"^[a-z_]{1,40}$")


def escapar(texto: str) -> str:
    return texto.replace("<", "&lt;").replace(">", "&gt;")


def como_dato(
    etiqueta: str,
    objeto: Any,
    *,
    atributos: Mapping[str, str] | None = None,
    maximo: int = MAXIMO_POR_DEFECTO,
) -> str:
    """`<etiqueta atributo="…">{dato escapado}</etiqueta>`. `etiqueta` es una constante del código."""
    if not _NOMBRE.match(etiqueta):
        raise ValueError("La etiqueta debe ser una constante en minúsculas.")
    contenido = objeto if isinstance(objeto, str) else json.dumps(objeto, ensure_ascii=False, default=str)
    contenido = escapar(contenido)
    if len(contenido) > maximo:
        contenido = contenido[:maximo] + MARCA_RECORTE
    marcas = "".join(
        f' {nombre}="{escapar(valor).replace(chr(34), "&quot;")}"' for nombre, valor in (atributos or {}).items()
    )
    return f"<{etiqueta}{marcas}>{contenido}</{etiqueta}>"
