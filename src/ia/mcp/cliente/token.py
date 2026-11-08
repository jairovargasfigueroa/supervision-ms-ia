"""Token del cliente confidencial `ia-agentes` (rol `agente_ia`) por *client credentials* (501).

Se guarda en memoria y se reutiliza hasta 30 s antes de que venza. `invalidar()` lo descarta (por ejemplo, si un
servidor MCP respondió `401`): la siguiente llamada pide otro. El secreto solo lo lee `ia-worker` y nunca se
registra.
"""

import asyncio
import logging
import time
from collections.abc import Callable

import httpx

registro = logging.getLogger("ia.mcp.cliente")

MARGEN_S = 30.0
TIEMPO_MAXIMO_S = 10.0


class TokenNoDisponible(Exception):
    """Keycloak no entregó el token de `ia-agentes` (el mensaje nunca incluye el secreto)."""


class TokenAgentes:
    def __init__(
        self,
        http: httpx.AsyncClient,
        url_token: str,
        client_id: str,
        client_secret: str,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._url = url_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._reloj = reloj
        self._token: str | None = None
        self._vence = 0.0
        self._candado = asyncio.Lock()

    async def token(self) -> str:
        async with self._candado:
            if self._token is not None and self._vence - self._reloj() > MARGEN_S:
                return self._token
            pedido_en = self._reloj()
            try:
                respuesta = await self._http.post(
                    self._url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                    timeout=TIEMPO_MAXIMO_S,
                )
            except httpx.HTTPError as error:
                raise TokenNoDisponible(f"Keycloak no respondió ({type(error).__name__}).") from error
            if respuesta.status_code != 200:
                raise TokenNoDisponible(f"Keycloak respondió {respuesta.status_code} al pedir el token de ia-agentes.")
            try:
                datos = respuesta.json()
            except ValueError as error:
                raise TokenNoDisponible("La respuesta del token no es JSON.") from error
            token = datos.get("access_token") if isinstance(datos, dict) else None
            if not isinstance(token, str) or not token:
                raise TokenNoDisponible("La respuesta del token no trae access_token.")
            self._token = token
            self._vence = pedido_en + float(datos.get("expires_in", 60))
            registro.info("Token de ia-agentes renovado (vence en %s s).", datos.get("expires_in", 60))
            return token

    def invalidar(self) -> None:
        self._token = None
        self._vence = 0.0
