"""Validación del token de Keycloak: firma (JWKS del realm), vencimiento, emisor y audiencia."""

import asyncio
from typing import Any, Protocol

import jwt
from jwt import PyJWKClient, PyJWKClientConnectionError, PyJWKClientError

from ia.auth.usuario import Usuario, roles_efectivos


class TokenInvalido(Exception):
    """No hay token o no es válido: 401 NO_AUTENTICADO."""


class ClavesNoDisponibles(Exception):
    """Keycloak no entregó el JWKS: 503 SERVICIO_NO_DISPONIBLE (no 401, para no cerrar la sesión del usuario)."""


class ProveedorClaves(Protocol):
    def clave_para(self, token: str) -> Any:
        """Clave pública con la que se firmó el token (bloqueante: se llama en un hilo)."""
        ...


class ProveedorJwks:
    """Claves del realm con `PyJWKClient` (las guarda en caché y vuelve a pedirlas si llega un `kid` nuevo)."""

    def __init__(self, url_jwks: str, tiempo_maximo: float = 5.0) -> None:
        self._cliente = PyJWKClient(url_jwks, cache_keys=True, lifespan=300, timeout=int(tiempo_maximo))

    def clave_para(self, token: str) -> Any:
        try:
            return self._cliente.get_signing_key_from_jwt(token).key
        except PyJWKClientConnectionError as error:
            raise ClavesNoDisponibles(str(error)) from error
        except PyJWKClientError as error:
            raise TokenInvalido(str(error)) from error
        except jwt.PyJWTError as error:
            raise TokenInvalido(str(error)) from error


class ValidadorToken:
    def __init__(self, proveedor: ProveedorClaves, emisor: str, audiencia: str) -> None:
        self._proveedor = proveedor
        self._emisor = emisor
        self._audiencia = audiencia

    async def validar(self, token: str) -> Usuario:
        clave = await asyncio.to_thread(self._proveedor.clave_para, token)
        try:
            datos: dict[str, Any] = jwt.decode(
                token,
                clave,
                algorithms=["RS256"],
                audience=self._audiencia,
                issuer=self._emisor,
                options={"require": ["exp", "iss", "sub"]},
            )
        except jwt.PyJWTError as error:
            raise TokenInvalido(str(error)) from error
        acceso = datos.get("realm_access")
        roles_token = acceso.get("roles", []) if isinstance(acceso, dict) else []
        roles = frozenset(r for r in roles_token if isinstance(r, str))
        azp = datos.get("azp") if isinstance(datos.get("azp"), str) else None
        return Usuario(id=str(datos["sub"]), roles=roles_efectivos(roles, azp), azp=azp)
