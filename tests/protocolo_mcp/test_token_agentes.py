"""Token de `ia-agentes` (501) contra un Keycloak simulado: caché hasta 30 s antes de vencer, renovación, invalidación
y errores sin mostrar el secreto."""

import logging
from urllib.parse import parse_qs

import httpx
import pytest

from ia.mcp.cliente.token import TokenAgentes, TokenNoDisponible

URL = "http://keycloak.prueba/realms/supervision/protocol/openid-connect/token"
SECRETO = "secreto-de-ia-agentes"


class KeycloakSimulado:
    def __init__(self, expira_en: int = 300, estado: int = 200) -> None:
        self.expira_en = expira_en
        self.estado = estado
        self.pedidos: list[dict[str, list[str]]] = []

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        self.pedidos.append(parse_qs(peticion.content.decode()))
        if self.estado != 200:
            return httpx.Response(self.estado, json={"error": "unauthorized_client"})
        return httpx.Response(200, json={"access_token": f"t{len(self.pedidos)}", "expires_in": self.expira_en})


class Reloj:
    def __init__(self) -> None:
        self.ahora = 1000.0

    def __call__(self) -> float:
        return self.ahora


def _token(keycloak: KeycloakSimulado, reloj: Reloj) -> TokenAgentes:
    http = httpx.AsyncClient(transport=httpx.MockTransport(keycloak))
    return TokenAgentes(http, URL, "ia-agentes", SECRETO, reloj=reloj)


async def test_client_credentials_y_cache_hasta_30_s_antes_de_vencer() -> None:
    keycloak, reloj = KeycloakSimulado(expira_en=300), Reloj()
    token = _token(keycloak, reloj)
    assert await token.token() == "t1"
    assert keycloak.pedidos[0] == {
        "grant_type": ["client_credentials"],
        "client_id": ["ia-agentes"],
        "client_secret": [SECRETO],
    }
    reloj.ahora += 269  # quedan 31 s
    assert await token.token() == "t1"
    reloj.ahora += 1  # quedan 30 s: se renueva
    assert await token.token() == "t2"
    assert len(keycloak.pedidos) == 2


async def test_invalidar_pide_otro_token() -> None:
    keycloak = KeycloakSimulado()
    token = _token(keycloak, Reloj())
    assert await token.token() == "t1"
    token.invalidar()
    assert await token.token() == "t2"


@pytest.mark.parametrize("estado", [401, 500])
async def test_keycloak_rechaza_o_falla_sin_mostrar_el_secreto(estado: int, caplog: pytest.LogCaptureFixture) -> None:
    token = _token(KeycloakSimulado(estado=estado), Reloj())
    with caplog.at_level(logging.DEBUG), pytest.raises(TokenNoDisponible) as error:
        await token.token()
    assert SECRETO not in str(error.value)
    assert all(SECRETO not in r.getMessage() for r in caplog.records)


async def test_keycloak_caido() -> None:
    def caido(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin conexión")

    token = TokenAgentes(httpx.AsyncClient(transport=httpx.MockTransport(caido)), URL, "ia-agentes", SECRETO)
    with pytest.raises(TokenNoDisponible, match="ConnectError"):
        await token.token()
