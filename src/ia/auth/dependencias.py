"""Dependencias de FastAPI: usuario del token y roles de cada ruta."""

import logging
from collections.abc import Awaitable, Callable

from fastapi import Depends, Request

from ia.auth.token import ClavesNoDisponibles, TokenInvalido, ValidadorToken
from ia.auth.usuario import ROLES_DEL_SERVICIO, Usuario
from ia.comun.errores import error_generico

registro = logging.getLogger("ia.auth")


async def autenticar_token(request: Request) -> Usuario:
    """Valida el `Authorization: Bearer`: 401 si falta o no es válido, 503 si Keycloak no entrega el JWKS."""
    encabezado = request.headers.get("authorization", "")
    tipo, _, token = encabezado.partition(" ")
    if tipo.lower() != "bearer" or not token.strip():
        raise error_generico(401, "Falta el token de acceso.")
    validador: ValidadorToken = request.app.state.validador_token
    try:
        return await validador.validar(token.strip())
    except TokenInvalido as error:
        registro.info("Token rechazado: %s", error)
        raise error_generico(401, "El token de acceso no es válido.") from error
    except ClavesNoDisponibles as error:
        registro.warning("No se pudo obtener el JWKS de Keycloak: %s", error)
        error_api = error_generico(503, "No se pudo validar el token: Keycloak no responde.")
        raise error_api from error


async def usuario_actual(request: Request) -> Usuario:
    """Usuario con token válido y un rol que el servicio atiende (`admin`, `gerente`, `residente`); si no, 403."""
    usuario = await autenticar_token(request)
    if not usuario.tiene_alguno(*ROLES_DEL_SERVICIO):
        raise error_generico(403, "El servicio de IA atiende solo a admin, gerente y residente.")
    return usuario


def exigir_roles(*roles: str) -> Callable[..., Awaitable[Usuario]]:
    """Dependencia de una ruta: el usuario debe tener alguno de `roles` (403 SIN_PERMISO si no)."""

    async def dependencia(usuario: Usuario = Depends(usuario_actual)) -> Usuario:  # noqa: B008
        if not usuario.tiene_alguno(*roles):
            raise error_generico(403, "Tu rol no permite esta acción.")
        return usuario

    return dependencia
