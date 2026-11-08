"""Sobre común de todo mensaje de RabbitMQ (convenciones, «Eventos y comandos»)."""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ia.comun.tiempo import leer_instante

VERSION_SOBRE = 1
"""Única versión del sobre que conoce este servicio."""


class MensajeInvalidoError(Exception):
    """El mensaje no se puede procesar nunca, por más que se reintente (no es JSON, sobre incompleto, versión
    desconocida o `datos` incompletos): va directo a `plataforma.fallidos`."""


@dataclass(frozen=True)
class Sobre:
    id: str
    tipo: str
    version: int
    datos: dict[str, Any]
    ocurrido_en: datetime | None = None
    origen: str | None = None
    correlacion_id: str | None = None


def leer_sobre(cuerpo: bytes) -> Sobre:
    """Lee y valida el sobre; si no sirve, lanza `MensajeInvalidoError`."""
    try:
        valor = json.loads(cuerpo.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise MensajeInvalidoError("El mensaje no es JSON.") from error
    if not isinstance(valor, dict):
        raise MensajeInvalidoError("El mensaje no es un objeto JSON.")
    id_ = valor.get("id")
    if not isinstance(id_, str) or not _es_uuid(id_):
        raise MensajeInvalidoError("Al sobre le falta el id (UUID).")
    tipo = valor.get("tipo")
    if not isinstance(tipo, str) or not tipo.strip():
        raise MensajeInvalidoError("Al sobre le falta el tipo.")
    version = valor.get("version")
    if version != VERSION_SOBRE or isinstance(version, bool):
        raise MensajeInvalidoError(f"Versión del sobre no soportada: {version}.")
    datos = valor.get("datos")
    if not isinstance(datos, dict):
        raise MensajeInvalidoError("Al sobre le faltan los datos.")
    for campo in ("ocurridoEn", "origen", "correlacionId"):
        if valor.get(campo) is not None and not isinstance(valor[campo], str):
            raise MensajeInvalidoError(f"El campo {campo} del sobre no es texto.")
    ocurrido_en = None
    if valor.get("ocurridoEn"):
        try:
            ocurrido_en = leer_instante(valor["ocurridoEn"])
        except ValueError as error:
            raise MensajeInvalidoError("El campo ocurridoEn del sobre no es una fecha ISO 8601 con zona.") from error
    return Sobre(
        id=id_,
        tipo=tipo,
        version=VERSION_SOBRE,
        datos=datos,
        ocurrido_en=ocurrido_en,
        origen=valor.get("origen"),
        correlacion_id=valor.get("correlacionId"),
    )


def _es_uuid(texto: str) -> bool:
    try:
        uuid.UUID(texto)
    except ValueError:
        return False
    return True
