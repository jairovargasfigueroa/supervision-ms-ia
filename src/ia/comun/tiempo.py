"""Tiempo del negocio: los instantes en UTC y el «hoy» en la zona del negocio (America/La_Paz)."""

from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

ZONA_NEGOCIO = "America/La_Paz"


class Reloj(Protocol):
    def ahora(self) -> datetime:
        """Instante actual con zona (UTC)."""
        ...

    def hoy(self) -> date:
        """Fecha de hoy en la zona del negocio."""
        ...


class RelojSistema:
    def __init__(self, zona: str = ZONA_NEGOCIO) -> None:
        self._zona = ZoneInfo(zona)

    def ahora(self) -> datetime:
        return datetime.now(UTC)

    def hoy(self) -> date:
        return datetime.now(self._zona).date()


class RelojFijo:
    """Reloj detenido en un instante (pruebas y comandos que necesitan una fecha fija)."""

    def __init__(self, ahora: datetime, zona: str = ZONA_NEGOCIO) -> None:
        if ahora.tzinfo is None:
            raise ValueError("El instante del reloj fijo necesita zona horaria.")
        self.momento = ahora
        self._zona = ZoneInfo(zona)

    def ahora(self) -> datetime:
        return self.momento

    def hoy(self) -> date:
        return self.momento.astimezone(self._zona).date()


def fecha_en_zona(momento: datetime, zona: str = ZONA_NEGOCIO) -> date:
    """Fecha de un instante en la zona dada."""
    return momento.astimezone(ZoneInfo(zona)).date()


def leer_instante(texto: str) -> datetime:
    """ISO 8601 con zona (`2026-10-28T14:30:00Z`); sin zona se rechaza."""
    momento = datetime.fromisoformat(texto)
    if momento.tzinfo is None:
        raise ValueError(f"Fecha sin zona horaria: {texto}")
    return momento.astimezone(UTC)


def iso_utc(momento: datetime) -> str:
    """`2026-11-15T14:30:00Z` (sin microsegundos si no los tiene)."""
    return momento.astimezone(UTC).isoformat().replace("+00:00", "Z")
