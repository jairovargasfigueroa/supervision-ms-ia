"""Puertos del constructor de reportes: ejecución de consultas, catálogo de Materiales, consultas guardadas y límite."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from ia.constructor_reportes.dominio.validacion import RecetaValidada


class ReportesNoDisponibles(Exception):
    """No responde `reportes_db` o `ia_db`, o la consulta pasó de 5 s (503 REPORTES_NO_DISPONIBLES)."""


class ServicioNoDisponible(Exception):
    """Falta lo necesario para un filtro: Materiales con filtro `material`, o el directorio con filtro `cliente`."""


class MaterialesNoDisponibles(Exception):
    """Materiales no respondió (o tardó más de 3 s)."""


@dataclass(frozen=True)
class ConsultaResuelta:
    """La receta validada con los nombres ya convertidos en ids (los arma el motor, nunca el modelo)."""

    receta: RecetaValidada
    hoy: date
    ids_material: tuple[str, ...] | None = None
    """Ids de los materiales cuyo nombre o código coincide con el filtro `material` (REST)."""
    ids_cliente: tuple[str, ...] | None = None
    """Ids de los clientes cuyo nombre coincide con el filtro `cliente` (REST)."""


@dataclass(frozen=True)
class FilasConsulta:
    claves: tuple[str, ...]
    """Claves de salida en orden (`material` y `cliente` traen el id)."""
    filas: list[tuple[Any, ...]]
    truncado: bool


class EjecutorConsultas(Protocol):
    async def ejecutar(self, consulta: ConsultaResuelta) -> FilasConsulta:
        """Hasta `limite` filas (pide `limite + 1` para saber si hay más). `ReportesNoDisponibles` si falla."""
        ...


@dataclass(frozen=True)
class MaterialCatalogo:
    id: str
    codigo: str
    nombre: str
    unidad: str


class CatalogoMateriales(Protocol):
    async def materiales(self, token: str) -> Mapping[str, MaterialCatalogo]:
        """Todos los materiales por id (con el token del usuario). `MaterialesNoDisponibles` si falla."""
        ...


@dataclass(frozen=True)
class ConsultaGuardada:
    id: str
    usuario_id: str
    texto: str | None
    atajo: str | None
    conjunto: str | None
    receta: dict[str, Any] | None
    resumen: str | None
    resumen_plantilla: bool
    resultado: str
    """`OK`, `NO_RECONOCIDA`, `RECETA_INVALIDA`, `ERROR`."""
    error_codigo: str | None
    total_filas: int
    proveedor_llm: str
    modelo: str
    duracion_ms: int
    creada_en: datetime


class RepositorioConsultas(Protocol):
    async def guardar(self, consulta: ConsultaGuardada) -> None: ...

    async def ultimas(self, usuario_id: str, limite: int) -> Sequence[ConsultaGuardada]:
        """Las del usuario, más recientes primero."""
        ...


class LimitadorConsultas(Protocol):
    async def contar(self, usuario_id: str, ahora: datetime) -> tuple[int, int]:
        """Suma 1 a la cuenta del minuto (atómico). Devuelve (cuenta, segundos hasta el próximo minuto)."""
        ...
