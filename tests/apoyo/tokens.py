"""Tokens firmados en la prueba con una clave RSA propia (sin Keycloak)."""

import time
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from ia.auth.token import ClavesNoDisponibles, ValidadorToken

EMISOR = "http://keycloak.prueba/realms/supervision"
AUDIENCIA = "plataforma-api"
ADMIN_ID = "a0000000-0000-4000-8000-000000000001"
GERENTE_ID = "a0000000-0000-4000-8000-000000000002"
RESIDENTE_ID = "a0000000-0000-4000-8000-000000000003"

_CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTRA_CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class ProveedorFijo:
    """Devuelve siempre la clave pública de la prueba; con `caido`, simula que Keycloak no responde."""

    def __init__(self, caido: bool = False) -> None:
        self.caido = caido

    def clave_para(self, token: str) -> Any:
        if self.caido:
            raise ClavesNoDisponibles("Keycloak no responde")
        return _CLAVE.public_key()


def validador(caido: bool = False) -> ValidadorToken:
    return ValidadorToken(ProveedorFijo(caido), emisor=EMISOR, audiencia=AUDIENCIA)


def token(
    roles: list[str],
    sub: str = ADMIN_ID,
    azp: str | None = "erp",
    *,
    emisor: str = EMISOR,
    audiencia: str = AUDIENCIA,
    vence_en: int = 300,
    otra_clave: bool = False,
) -> str:
    ahora = int(time.time())
    datos: dict[str, Any] = {
        "sub": sub,
        "iss": emisor,
        "aud": audiencia,
        "iat": ahora,
        "exp": ahora + vence_en,
        "realm_access": {"roles": roles},
    }
    if azp is not None:
        datos["azp"] = azp
    return jwt.encode(datos, _OTRA_CLAVE if otra_clave else _CLAVE, algorithm="RS256")


def encabezado(roles: list[str], **opciones: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(roles, **opciones)}"}
