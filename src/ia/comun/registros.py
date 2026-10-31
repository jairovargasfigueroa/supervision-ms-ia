"""Registros: texto en desarrollo y JSON en producción, con `requestId` o `correlacionId` en cada línea."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

# X-Request-Id de la petición HTTP o de la corrida de la carga inicial.
id_peticion: ContextVar[str | None] = ContextVar("id_peticion", default=None)
# correlacionId del evento que se está procesando.
id_correlacion: ContextVar[str | None] = ContextVar("id_correlacion", default=None)


class _FiltroContexto(logging.Filter):
    def filter(self, registro: logging.LogRecord) -> bool:
        registro.request_id = id_peticion.get()
        registro.correlacion_id = id_correlacion.get()
        return True


class FormatoJson(logging.Formatter):
    def format(self, registro: logging.LogRecord) -> str:
        datos: dict[str, object] = {
            "ts": datetime.fromtimestamp(registro.created, UTC).isoformat().replace("+00:00", "Z"),
            "nivel": registro.levelname,
            "registro": registro.name,
            "mensaje": registro.getMessage(),
        }
        if request_id := getattr(registro, "request_id", None):
            datos["requestId"] = request_id
        if correlacion_id := getattr(registro, "correlacion_id", None):
            datos["correlacionId"] = correlacion_id
        if registro.exc_info:
            datos["error"] = self.formatException(registro.exc_info)
        return json.dumps(datos, ensure_ascii=False)


class FormatoTexto(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s%(contexto)s %(message)s")

    def format(self, registro: logging.LogRecord) -> str:
        partes = []
        if request_id := getattr(registro, "request_id", None):
            partes.append(f"req={request_id}")
        if correlacion_id := getattr(registro, "correlacion_id", None):
            partes.append(f"corr={correlacion_id}")
        registro.contexto = f" [{' '.join(partes)}]" if partes else ""
        return super().format(registro)


def configurar_registros(json_: bool, nivel: int = logging.INFO) -> None:
    """Una sola salida a stdout para la raíz y los registros de Uvicorn."""
    manejador = logging.StreamHandler(sys.stdout)
    manejador.setFormatter(FormatoJson() if json_ else FormatoTexto())
    manejador.addFilter(_FiltroContexto())
    raiz = logging.getLogger()
    raiz.handlers[:] = [manejador]
    raiz.setLevel(nivel)
    for nombre in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        registro = logging.getLogger(nombre)
        registro.handlers[:] = []
        registro.propagate = True
    for ruidoso in ("aio_pika", "aiormq", "apscheduler", "httpx", "pymongo"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)
