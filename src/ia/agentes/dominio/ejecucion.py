"""Ejecuciones de los agentes y sugerencias (502): estados y entidades (sin base de datos)."""

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final

# Ejecución
EN_CURSO: Final = "EN_CURSO"
TERMINADA: Final = "TERMINADA"
FALLIDA: Final = "FALLIDA"
ESTADOS_EJECUCION: Final = (EN_CURSO, TERMINADA, FALLIDA)
PROGRAMADA: Final = "PROGRAMADA"
MANUAL: Final = "MANUAL"

# Errores de una ejecución FALLIDA
SIN_HERRAMIENTAS: Final = "SIN_HERRAMIENTAS"
REPORTE_INVALIDO: Final = "REPORTE_INVALIDO"
EJECUCION_TIEMPO_AGOTADO: Final = "EJECUCION_TIEMPO_AGOTADO"
INTERRUMPIDA: Final = "INTERRUMPIDA"
LLM_NO_DISPONIBLE: Final = "LLM_NO_DISPONIBLE"

# Errores de un paso
HERRAMIENTA_NO_PERMITIDA: Final = "HERRAMIENTA_NO_PERMITIDA"
ARGUMENTO_INVALIDO: Final = "ARGUMENTO_INVALIDO"
SERVIDOR_NO_DISPONIBLE: Final = "SERVIDOR_NO_DISPONIBLE"
LIMITE_ALCANZADO: Final = "LIMITE_ALCANZADO"

# Sugerencia
PENDIENTE: Final = "PENDIENTE"
APROBADA: Final = "APROBADA"
RECHAZADA: Final = "RECHAZADA"
ESTADOS_SUGERENCIA: Final = (PENDIENTE, APROBADA, RECHAZADA)
COMENTARIO_MAXIMO: Final = 300
MOTIVO_MINIMO: Final = 10
MOTIVO_MAXIMO: Final = 500


@dataclass(frozen=True)
class ErrorEjecucion:
    codigo: str
    mensaje: str


@dataclass(frozen=True)
class Paso:
    numero: int
    servidor: str
    """`obras`, `materiales`, `finanzas` o `ia`."""
    herramienta: str
    argumentos: str
    """Texto JSON de los argumentos que pidió el modelo (o los del paso inicial)."""
    resultado_resumen: str
    """Primeros 500 caracteres del resultado y el número de filas (no se guarda el resultado completo)."""
    en: datetime
    duracion_ms: int
    error: str | None = None


@dataclass(frozen=True)
class Evidencia:
    herramienta: str
    dato: str


@dataclass(frozen=True)
class HallazgoGuardado:
    obra_id: str
    obra_nombre: str
    tipo: str
    severidad: str
    situacion: str
    causa_probable: str
    evidencia: tuple[Evidencia, ...]


@dataclass(frozen=True)
class Ejecucion:
    id: str
    agente: str
    origen: str
    estado: str
    solicitada_por: str | None
    iniciada_en: datetime
    proveedor_llm: str
    modelo: str
    tomada_en: datetime | None = None
    terminada_en: datetime | None = None
    resumen: str | None = None
    error: ErrorEjecucion | None = None
    pasos: tuple[Paso, ...] = field(default=())
    hallazgos: tuple[HallazgoGuardado, ...] = field(default=())

    @staticmethod
    def nueva(
        agente: str, origen: str, solicitada_por: str | None, ahora: datetime, proveedor_llm: str, modelo: str
    ) -> "Ejecucion":
        """`EN_CURSO`, sin tomar; guarda el proveedor y el modelo de lenguaje que va a usar."""
        return Ejecucion(
            id=str(uuid.uuid4()),
            agente=agente,
            origen=origen,
            estado=EN_CURSO,
            solicitada_por=solicitada_por,
            iniciada_en=ahora,
            proveedor_llm=proveedor_llm,
            modelo=modelo,
        )

    def terminar(self, resumen: str, hallazgos: tuple[HallazgoGuardado, ...], en: datetime) -> "Ejecucion":
        return replace(self, estado=TERMINADA, resumen=resumen, hallazgos=hallazgos, terminada_en=en)

    def fallar(self, codigo: str, mensaje: str, en: datetime) -> "Ejecucion":
        return replace(self, estado=FALLIDA, error=ErrorEjecucion(codigo, mensaje), terminada_en=en)


@dataclass(frozen=True)
class Sugerencia:
    id: str
    ejecucion_id: str
    agente: str
    obra_id: str
    obra_nombre: str
    accion: str
    descripcion: str
    hallazgo_indice: int
    creada_en: datetime
    estado: str = PENDIENTE
    decidida_por: str | None = None
    decidida_en: datetime | None = None
    comentario: str | None = None
