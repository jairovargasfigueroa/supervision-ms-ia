"""Errores de la API en formato Problem Details (RFC 9457) con `codigo` (convenciones, «Errores REST»)."""

from dataclasses import dataclass, field

# Código genérico y título de cada estado, cuando no hay un código propio.
GENERICOS: dict[int, tuple[str, str]] = {
    400: ("DATOS_INVALIDOS", "Datos inválidos"),
    401: ("NO_AUTENTICADO", "No autenticado"),
    403: ("SIN_PERMISO", "Sin permiso"),
    404: ("RUTA_NO_ENCONTRADA", "Ruta no encontrada"),
    413: ("ARCHIVO_DEMASIADO_GRANDE", "Archivo demasiado grande"),
    429: ("DEMASIADAS_PETICIONES", "Demasiadas peticiones"),
    500: ("ERROR_INTERNO", "Error interno"),
    503: ("SERVICIO_NO_DISPONIBLE", "Servicio no disponible"),
    504: ("TIEMPO_AGOTADO", "Tiempo agotado"),
}


@dataclass(frozen=True)
class ErrorCampo:
    campo: str
    mensaje: str


@dataclass
class ErrorApi(Exception):
    """Error que la API responde como `application/problem+json`."""

    estado: int
    codigo: str
    titulo: str
    detalle: str | None = None
    errores: list[ErrorCampo] = field(default_factory=list)
    encabezados: dict[str, str] = field(default_factory=dict)
    """Encabezados de la respuesta (por ejemplo `Retry-After` con `429`)."""

    def __str__(self) -> str:
        return f"{self.estado} {self.codigo}: {self.detalle or self.titulo}"


def error_generico(estado: int, detalle: str | None = None) -> ErrorApi:
    """Error con el código genérico de su estado (por ejemplo `403 SIN_PERMISO`)."""
    codigo, titulo = GENERICOS.get(estado, GENERICOS[500])
    return ErrorApi(estado=estado, codigo=codigo, titulo=titulo, detalle=detalle)
