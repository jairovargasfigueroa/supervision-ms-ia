"""Conversación del constructor de reportes (505): estado de un pedido que puede llevar preguntas de aclaración y
seguimientos sobre el último reporte.

Reglas (505, «Estado» y «Flujo de un turno»):
- Solo su dueño la continúa; dura 30 minutos sin uso (TTL en `ia_db.conversacion_reporte`).
- Hasta 5 preguntas de aclaración por conversación; se guardan los últimos 20 turnos.
- `receta`: la última receta válida (la que ajustan los seguimientos). `duda`: pregunta del código pendiente, con la
  receta que espera la respuesta. `aceptados`: términos que el usuario aceptó «tal cual» (no se vuelven a preguntar).
"""

import difflib
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

PREGUNTAS_MAXIMAS = 5
TURNOS_MAXIMOS = 20
TURNOS_AL_MODELO = 10
MINUTOS_DE_VIDA = 30
OPCIONES_MAXIMAS = 6
OPCION_TODOS = "Todos"
OPCION_NINGUNO = "Ninguno"

# Roles y tipos de turno.
USUARIO = "USUARIO"
ASISTENTE = "ASISTENTE"
PEDIDO = "PEDIDO"
REPORTE = "REPORTE"
ACLARACION = "ACLARACION"
NO_RECONOCIDO = "NO_RECONOCIDO"


@dataclass(frozen=True)
class Turno:
    rol: str
    texto: str
    tipo: str
    en: datetime


@dataclass(frozen=True)
class Duda:
    """Pregunta del código: un nombre que coincide con varios (`VARIOS`) o con ninguno (`NINGUNO`)."""

    campo: str
    """`obra`, `cliente` o `material`."""
    termino: str
    clase: str
    opciones: tuple[str, ...]
    receta: dict[str, Any]
    """Receta (ya validada, en JSON) que espera la respuesta."""


@dataclass(frozen=True)
class Aclaracion:
    pregunta: str
    opciones: tuple[str, ...]
    preguntas_restantes: int


@dataclass
class Conversacion:
    id: str
    usuario_id: str
    creada_en: datetime
    actualizada_en: datetime
    turnos: list[Turno] = field(default_factory=list)
    receta: dict[str, Any] | None = None
    preguntas: int = 0
    duda: Duda | None = None
    aceptados: list[str] = field(default_factory=list)

    @property
    def expira_en(self) -> datetime:
        return self.actualizada_en + timedelta(minutes=MINUTOS_DE_VIDA)

    @property
    def puede_preguntar(self) -> bool:
        return self.preguntas < PREGUNTAS_MAXIMAS

    @property
    def preguntas_restantes(self) -> int:
        return max(0, PREGUNTAS_MAXIMAS - self.preguntas)

    @property
    def es_nueva(self) -> bool:
        """Sin turnos anteriores al actual (solo el pedido que acaba de llegar)."""
        return len(self.turnos) <= 1 and self.receta is None

    def agregar(self, rol: str, texto: str, tipo: str, ahora: datetime) -> None:
        self.turnos.append(Turno(rol, texto[:600], tipo, ahora))
        del self.turnos[:-TURNOS_MAXIMOS]
        self.actualizada_en = ahora

    def preguntar(self, pregunta: str, opciones: tuple[str, ...], ahora: datetime) -> Aclaracion:
        self.preguntas += 1
        self.agregar(ASISTENTE, _texto_aclaracion(pregunta, opciones), ACLARACION, ahora)
        return Aclaracion(pregunta, opciones, self.preguntas_restantes)

    def turnos_recientes(self) -> list[dict[str, str]]:
        """Los últimos turnos para el modelo (sin el pedido actual, que va aparte)."""
        anteriores = self.turnos[:-1] if self.turnos and self.turnos[-1].rol == USUARIO else self.turnos
        return [{"rol": t.rol, "texto": t.texto} for t in anteriores[-TURNOS_AL_MODELO:]]


def _texto_aclaracion(pregunta: str, opciones: tuple[str, ...]) -> str:
    return pregunta + (f" Opciones: {', '.join(opciones)}." if opciones else "")


def normalizar(texto: str) -> str:
    """Sin mayúsculas ni tildes (la misma regla que el motor)."""
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).casefold().strip()


def elegida(duda: Duda, texto: str) -> str | None:
    """La opción de la duda que coincide con el texto del usuario (sin mayúsculas ni tildes), o `None`."""
    buscado = normalizar(texto).strip(" .¡!¿?")
    return next((o for o in duda.opciones if normalizar(o) == buscado), None)


def parecidos(termino: str, nombres: list[str], cantidad: int = 5) -> list[str]:
    """Nombres parecidos a un término que no coincide con ninguno (`difflib`, corte 0,6), por palabra o completo."""
    por_normalizado = {normalizar(n): n for n in nombres}
    buscado = normalizar(termino)
    candidatos = set(difflib.get_close_matches(buscado, list(por_normalizado), n=cantidad, cutoff=0.6))
    for normalizado in por_normalizado:
        palabras = normalizado.split()
        if difflib.get_close_matches(buscado, palabras, n=1, cutoff=0.75):
            candidatos.add(normalizado)
    return sorted(por_normalizado[c] for c in candidatos)[:cantidad]


def con_termino_elegido(receta: dict[str, Any], campo: str, termino: str, nombre: str) -> dict[str, Any]:
    """La receta con el término del filtro `campo` reemplazado por el nombre exacto que eligió el usuario."""
    filtros = []
    for f in receta.get("filtros", []):
        if f.get("campo") == campo:
            if isinstance(f.get("valor"), list):
                f = {**f, "valor": [nombre if v == termino else v for v in f["valor"]]}
            elif f.get("valor") == termino:
                f = {**f, "valor": nombre}
        filtros.append(f)
    return {**receta, "filtros": filtros}
