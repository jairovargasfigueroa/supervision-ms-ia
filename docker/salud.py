"""HEALTHCHECK de la imagen (601), solo con la biblioteca estándar.

Con `ia-api` (proceso 1 del contenedor), `GET http://127.0.0.1:<IA_PUERTO>/salud` debe responder 200. Con otro
proceso (`ia-worker`, `ia-carga-inicial`) basta con que esté vivo: no atienden HTTP.
"""

import os
import sys
import urllib.request
from pathlib import Path


def proceso_principal() -> list[str]:
    try:
        crudo = Path("/proc/1/cmdline").read_bytes()
    except OSError:
        return []
    return [parte.decode(errors="replace") for parte in crudo.split(b"\0") if parte]


def main() -> int:
    if not any(Path(parte).name == "ia-api" for parte in proceso_principal()):
        return 0
    url = f"http://127.0.0.1:{os.environ.get('IA_PUERTO', '8085')}/salud"
    try:
        with urllib.request.urlopen(url, timeout=3) as respuesta:
            return 0 if respuesta.status == 200 else 1
    except OSError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
