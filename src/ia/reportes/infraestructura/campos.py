"""Lectura tolerante de campos JSON (eventos y respuestas REST): ignora lo desconocido y valida lo que se usa."""

import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ia.comun.tiempo import leer_instante


class DatoInvalido(ValueError):
    """Falta un campo obligatorio o no tiene el formato esperado."""


def objeto(datos: dict[str, Any], campo: str) -> dict[str, Any]:
    valor = datos.get(campo)
    if not isinstance(valor, dict):
        raise DatoInvalido(f"Falta {campo}.")
    return valor


def texto(datos: dict[str, Any], campo: str) -> str:
    valor = datos.get(campo)
    if not isinstance(valor, str) or not valor.strip():
        raise DatoInvalido(f"Falta {campo}.")
    return valor


def texto_opcional(datos: dict[str, Any], campo: str) -> str | None:
    valor = datos.get(campo)
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise DatoInvalido(f"{campo} no es texto.")
    return valor


def uuid_(datos: dict[str, Any], campo: str) -> str:
    valor = texto(datos, campo)
    try:
        return str(uuid.UUID(valor))
    except ValueError as error:
        raise DatoInvalido(f"{campo} no es un UUID.") from error


def uuid_opcional(datos: dict[str, Any], campo: str) -> str | None:
    return None if datos.get(campo) is None else uuid_(datos, campo)


def decimal(datos: dict[str, Any], campo: str) -> Decimal:
    valor = datos.get(campo)
    if isinstance(valor, bool) or not isinstance(valor, int | float | str):
        raise DatoInvalido(f"Falta {campo} (número).")
    try:
        numero = Decimal(str(valor))
    except InvalidOperation as error:
        raise DatoInvalido(f"{campo} no es un número.") from error
    if not numero.is_finite():
        raise DatoInvalido(f"{campo} no es un número.")
    return numero


def decimal_opcional(datos: dict[str, Any], campo: str) -> Decimal | None:
    return None if datos.get(campo) is None else decimal(datos, campo)


def entero(datos: dict[str, Any], campo: str) -> int:
    valor = datos.get(campo)
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise DatoInvalido(f"Falta {campo} (entero).")
    return valor


def fecha(datos: dict[str, Any], campo: str) -> date:
    try:
        return date.fromisoformat(texto(datos, campo))
    except ValueError as error:
        raise DatoInvalido(f"{campo} no es una fecha AAAA-MM-DD.") from error


def fecha_opcional(datos: dict[str, Any], campo: str) -> date | None:
    return None if datos.get(campo) is None else fecha(datos, campo)


def instante(datos: dict[str, Any], campo: str) -> datetime:
    try:
        return leer_instante(texto(datos, campo))
    except ValueError as error:
        raise DatoInvalido(f"{campo} no es una fecha ISO 8601 con zona.") from error


def periodo(datos: dict[str, Any], campo: str) -> str:
    valor = texto(datos, campo)
    if len(valor) != 7 or valor[4] != "-" or not (valor[:4] + valor[5:]).isdigit():
        raise DatoInvalido(f"{campo} no es un periodo AAAA-MM.")
    return valor


def periodo_opcional(datos: dict[str, Any], campo: str) -> str | None:
    return None if datos.get(campo) is None else periodo(datos, campo)


def booleano(datos: dict[str, Any], campo: str, por_defecto: bool = False) -> bool:
    valor = datos.get(campo, por_defecto)
    if not isinstance(valor, bool):
        raise DatoInvalido(f"{campo} no es booleano.")
    return valor


def lista_de_objetos(datos: dict[str, Any], campo: str) -> list[dict[str, Any]]:
    valor = datos.get(campo)
    if not isinstance(valor, list) or not all(isinstance(elemento, dict) for elemento in valor):
        raise DatoInvalido(f"Falta {campo} (lista).")
    return valor
