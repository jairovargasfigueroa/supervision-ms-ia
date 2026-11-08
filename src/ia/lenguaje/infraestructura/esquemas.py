"""JSON Schema que acepta cada proveedor para herramientas y salida estructurada.

Los esquemas estrictos de Anthropic exigen `additionalProperties: false` en cada objeto y no aceptan `minLength`,
`maxLength`, `minimum`, `maximum` ni `minItems` mayor que 1 (verificado el 31/10/2026). Se quitan del esquema que
se envía; Pydantic los sigue aplicando al validar la respuesta.
"""

import copy
from typing import Any

_NO_ACEPTA_ANTHROPIC = ("minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")


def adaptar_esquema(esquema: dict[str, Any], proveedor: str) -> dict[str, Any]:
    """Copia adaptada (nunca cambia el original)."""
    copia = copy.deepcopy(esquema)
    if proveedor == "anthropic":
        _adaptar_anthropic(copia)
    return copia


def _adaptar_anthropic(nodo: Any) -> None:
    if isinstance(nodo, list):
        for elemento in nodo:
            _adaptar_anthropic(elemento)
        return
    if not isinstance(nodo, dict):
        return
    for clave in _NO_ACEPTA_ANTHROPIC:
        nodo.pop(clave, None)
    if isinstance(nodo.get("minItems"), int) and nodo["minItems"] > 1:
        nodo.pop("minItems")
    if nodo.get("type") == "object" or "properties" in nodo:
        nodo["additionalProperties"] = False
    for clave, valor in nodo.items():
        if clave in ("properties", "$defs", "definitions") and isinstance(valor, dict):
            for subesquema in valor.values():
                _adaptar_anthropic(subesquema)
        elif clave not in ("enum", "const", "required", "default", "examples"):
            _adaptar_anthropic(valor)
