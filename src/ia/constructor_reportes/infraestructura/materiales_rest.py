"""Catálogo de Materiales para los nombres de los reportes (503).

`GET {IA_MATERIALES_URL}/materiales?pagina=N&tamano=100` (todas las páginas) **con el token del usuario** que reenvía
el gateway; 3 s por llamada y caché de 5 minutos (el catálogo es el mismo para todos los roles internos). No alimenta
la base de reportes.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from typing import Any

import httpx

from ia.comun.registros import id_peticion
from ia.constructor_reportes.aplicacion.puertos import MaterialCatalogo, MaterialesNoDisponibles

registro = logging.getLogger("ia.constructor_reportes")

TAMANO_PAGINA = 100
TIEMPO_MAXIMO_S = 3.0
DURACION_CACHE_S = 300.0
PAGINAS_MAXIMAS = 100


class CatalogoMaterialesRest:
    def __init__(
        self,
        url: str,
        transporte: httpx.AsyncBaseTransport | None = None,
        reloj: Any = time.monotonic,
    ) -> None:
        self._url = url.rstrip("/")
        self._transporte = transporte
        self._reloj = reloj
        self._cache: tuple[float, dict[str, MaterialCatalogo]] | None = None
        self._candado = asyncio.Lock()

    async def materiales(self, token: str) -> Mapping[str, MaterialCatalogo]:
        async with self._candado:
            if self._cache is not None and self._reloj() - self._cache[0] < DURACION_CACHE_S:
                return self._cache[1]
            materiales = await self._pedir(token)
            self._cache = (self._reloj(), materiales)
            return materiales

    async def _pedir(self, token: str) -> dict[str, MaterialCatalogo]:
        encabezados = {"Authorization": f"Bearer {token}"}
        if request_id := id_peticion.get():
            encabezados["X-Request-Id"] = request_id
        materiales: dict[str, MaterialCatalogo] = {}
        try:
            async with httpx.AsyncClient(
                base_url=self._url, timeout=TIEMPO_MAXIMO_S, transport=self._transporte, headers=encabezados
            ) as cliente:
                for pagina in range(1, PAGINAS_MAXIMAS + 1):
                    respuesta = await cliente.get("/materiales", params={"pagina": pagina, "tamano": TAMANO_PAGINA})
                    respuesta.raise_for_status()
                    cuerpo = respuesta.json()
                    items = cuerpo.get("items") or []
                    for item in items:
                        material = MaterialCatalogo(
                            id=str(item["id"]),
                            codigo=str(item.get("codigo") or ""),
                            nombre=str(item.get("nombre") or item["id"]),
                            unidad=str(item.get("unidad") or ""),
                        )
                        materiales[material.id] = material
                    if not items or len(materiales) >= int(cuerpo.get("total") or 0):
                        break
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            registro.warning("Materiales no respondió al pedir el catálogo: %s", type(error).__name__)
            raise MaterialesNoDisponibles("Materiales no respondió.") from error
        return materiales
