"""Conexión a `ia_db` (MongoDB) y registro de índices.

En la 401 solo se crea la conexión: las colecciones (y sus índices) las agregan la 402 (`prediccion`) y la 403
(`consumo_atipico`) con `registrar_indice`; `ia-api` e `ia-worker` los aseguran al arrancar.
"""

from dataclasses import dataclass
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase


@dataclass(frozen=True)
class IndiceMongo:
    coleccion: str
    claves: tuple[tuple[str, int], ...]
    nombre: str
    unico: bool = False


_INDICES: list[IndiceMongo] = []


def registrar_indice(indice: IndiceMongo) -> None:
    """Lo llama cada módulo al importarse; el mismo nombre dos veces se registra una sola vez."""
    if all(existente.nombre != indice.nombre or existente.coleccion != indice.coleccion for existente in _INDICES):
        _INDICES.append(indice)


def indices_registrados() -> tuple[IndiceMongo, ...]:
    return tuple(_INDICES)


def crear_cliente_mongo(url: str) -> AsyncMongoClient[dict[str, Any]]:
    """El cliente no se conecta hasta la primera operación (arrancar sin MongoDB no falla)."""
    return AsyncMongoClient(url, tz_aware=True, serverSelectionTimeoutMS=5000)


async def asegurar_indices(base: AsyncDatabase[dict[str, Any]]) -> int:
    """Crea los índices registrados (idempotente). Devuelve cuántos revisó."""
    for indice in _INDICES:
        await base[indice.coleccion].create_index(list(indice.claves), name=indice.nombre, unique=indice.unico)
    return len(_INDICES)
