"""Conector MCP de los agentes (502): un `ClienteMcp` (501) por servidor y por ejecución, con el token de
`ia-agentes` y `X-Request-Id` = id de la ejecución."""

from collections.abc import Mapping
from typing import Any

import httpx2

from ia.mcp.cliente.cliente import ClienteMcp, FalloMcp, HerramientaPublicada, ResultadoHerramienta
from ia.mcp.cliente.token import TokenAgentes


class SesionMcpReal:
    def __init__(
        self,
        urls: Mapping[str, str],
        token: TokenAgentes,
        ejecucion_id: str,
        transportes: Mapping[str, httpx2.AsyncBaseTransport] | None = None,
    ) -> None:
        self._urls = urls
        self._token = token
        self._ejecucion_id = ejecucion_id
        self._transportes = transportes or {}
        self._clientes: dict[str, ClienteMcp] = {}

    def _cliente(self, servidor: str) -> ClienteMcp:
        if servidor not in self._urls:
            raise FalloMcp(f"No hay URL configurada para el servidor {servidor}.")
        if servidor not in self._clientes:
            self._clientes[servidor] = ClienteMcp(
                servidor,
                self._urls[servidor],
                self._token,
                self._ejecucion_id,
                transporte=self._transportes.get(servidor),
            )
        return self._clientes[servidor]

    async def listar(self, servidor: str) -> list[HerramientaPublicada]:
        return await self._cliente(servidor).listar_herramientas()

    async def llamar(self, servidor: str, herramienta: str, argumentos: dict[str, Any]) -> ResultadoHerramienta:
        return await self._cliente(servidor).llamar(herramienta, argumentos)


class ConectorMcpReal:
    def __init__(
        self,
        urls: Mapping[str, str],
        token: TokenAgentes,
        transportes: Mapping[str, httpx2.AsyncBaseTransport] | None = None,
    ) -> None:
        self._urls = urls
        self._token = token
        self._transportes = transportes

    def sesion(self, ejecucion_id: str) -> SesionMcpReal:
        return SesionMcpReal(self._urls, self._token, ejecucion_id, self._transportes)
