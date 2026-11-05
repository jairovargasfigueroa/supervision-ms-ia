"""TLS de las conexiones salientes (601): una CA adicional opcional (`IA_CA_EXTRA`).

El TLS lo pide la URL de cada conexión: `https://` (Keycloak, servicios, MCP, Ollama), `amqps://` (RabbitMQ),
`tls=true`/`ssl=true` o `mongodb+srv://` (MongoDB) y `sslmode=require|verify-ca|verify-full` (PostgreSQL).

Con `IA_CA_EXTRA` (ruta de un PEM; en el ensayo local, la CA de Caddy) esas conexiones confían en las CA públicas de
`certifi` **y además** en esa. Cada comando la fija una vez al arrancar con `configurar_tls`; sin ella, cada librería
usa sus CA de siempre (estas funciones devuelven «nada que cambiar»).
"""

import ssl
import tempfile
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import certifi

from ia.config import ConfiguracionInvalida

_SSLMODE_CON_TLS = frozenset({"require", "verify-ca", "verify-full"})
_ca_extra: Path | None = None


def configurar_tls(ca_extra: str | None) -> None:
    """Fija la CA adicional del proceso (vacía o `None`: ninguna). `ConfiguracionInvalida` si no es un PEM legible."""
    global _ca_extra
    if ca_extra is None or not ca_extra.strip():
        _ca_extra = None
        return
    ruta = Path(ca_extra.strip())
    if not ruta.is_file():
        raise ConfiguracionInvalida("IA_CA_EXTRA no apunta a un archivo.")
    try:
        ssl.create_default_context().load_verify_locations(cafile=str(ruta))
    except (ssl.SSLError, ValueError) as error:
        raise ConfiguracionInvalida("IA_CA_EXTRA no es un certificado PEM válido.") from error
    _ca_extra = ruta


def ca_extra() -> Path | None:
    return _ca_extra


def contexto_ssl() -> ssl.SSLContext | None:
    """Contexto con las CA de `certifi` y la adicional; `None` sin `IA_CA_EXTRA` (la librería usa el suyo)."""
    return None if _ca_extra is None else _contexto(_ca_extra)


def verificar_http() -> ssl.SSLContext | bool:
    """Valor de `verify=` para `httpx` y `httpx2`: el contexto con la CA adicional, o `True` (lo de siempre)."""
    contexto = contexto_ssl()
    return True if contexto is None else contexto


def opciones_mongo(url: str) -> dict[str, Any]:
    """`tlsCAFile` para `AsyncMongoClient` si la URL pide TLS y no trae ya su CA (pymongo activa TLS con esa opción,
    por eso solo se agrega cuando la URL lo pide)."""
    if _ca_extra is None:
        return {}
    partes = urlsplit(url)
    opciones = {clave.lower(): valores[-1].lower() for clave, valores in parse_qs(partes.query).items()}
    pide_tls = partes.scheme == "mongodb+srv" or opciones.get("tls") == "true" or opciones.get("ssl") == "true"
    if not pide_tls or "tlscafile" in opciones:
        return {}
    return {"tlsCAFile": str(_paquete(_ca_extra))}


def argumentos_postgres(url: str) -> dict[str, str]:
    """`sslrootcert` para psycopg (libpq) si la URL pide TLS (`sslmode`) y no trae ya su CA."""
    if _ca_extra is None:
        return {}
    opciones = {clave: valores[-1] for clave, valores in parse_qs(urlsplit(url).query).items()}
    if opciones.get("sslmode", "").lower() not in _SSLMODE_CON_TLS or "sslrootcert" in opciones:
        return {}
    return {"sslrootcert": str(_paquete(_ca_extra))}


@cache
def _contexto(ruta: Path) -> ssl.SSLContext:
    contexto = ssl.create_default_context(cafile=certifi.where())
    contexto.load_verify_locations(cafile=str(ruta))
    return contexto


@cache
def _paquete(ruta: Path) -> Path:
    """Un PEM con las CA de `certifi` y la adicional (libpq y pymongo leen un solo archivo de CA)."""
    contenido = Path(certifi.where()).read_bytes().rstrip(b"\n") + b"\n" + ruta.read_bytes()
    with tempfile.NamedTemporaryFile("wb", prefix="ia-ca-", suffix=".pem", delete=False) as archivo:
        archivo.write(contenido)
    return Path(archivo.name)
