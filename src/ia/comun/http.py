"""HTTP común: Problem Details, `X-Request-Id` y encabezados de seguridad."""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ia.comun.errores import ErrorApi, ErrorCampo, error_generico
from ia.comun.registros import id_peticion

ENCABEZADO_REQUEST_ID = "X-Request-Id"
TIPO_PROBLEMA = "application/problem+json"
_REQUEST_ID_VALIDO = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

registro = logging.getLogger("ia.http")

# Comprueba el token de la petición (lanza ErrorApi 401/503 si no es válido). Lo instala `ia.auth`.
Autenticador = Callable[[Request], Awaitable[object]]


def respuesta_problema(error: ErrorApi) -> JSONResponse:
    cuerpo: dict[str, Any] = {
        "type": "about:blank",
        "title": error.titulo,
        "status": error.estado,
        "codigo": error.codigo,
    }
    if error.detalle:
        cuerpo["detail"] = error.detalle
    if error.errores:
        cuerpo["errores"] = [{"campo": e.campo, "mensaje": e.mensaje} for e in error.errores]
    return JSONResponse(cuerpo, status_code=error.estado, media_type=TIPO_PROBLEMA, headers=error.encabezados or None)


class MiddlewareRequestId:
    """Lee `X-Request-Id` (o crea uno), lo deja en el contexto de los registros y lo devuelve en la respuesta.

    Agrega también `X-Content-Type-Options: nosniff` y `Referrer-Policy: no-referrer`.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        recibido = None
        for nombre, valor in scope.get("headers", []):
            if nombre == b"x-request-id":
                recibido = valor.decode("latin-1")
                break
        request_id = recibido if recibido and _REQUEST_ID_VALIDO.match(recibido) else str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        marca = id_peticion.set(request_id)

        async def enviar(mensaje: Message) -> None:
            if mensaje["type"] == "http.response.start":
                encabezados = list(mensaje.get("headers", []))
                encabezados.append((b"x-request-id", request_id.encode("latin-1")))
                encabezados.append((b"x-content-type-options", b"nosniff"))
                encabezados.append((b"referrer-policy", b"no-referrer"))
                mensaje["headers"] = encabezados
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        finally:
            id_peticion.reset(marca)


def instalar_http(app: FastAPI, autenticador: Autenticador | None = None) -> None:
    """Middleware y manejadores de errores.

    Con `autenticador`, una ruta inexistente responde 404 solo con token válido (sin token, 401: convenciones,
    «Errores REST»).
    """
    app.add_middleware(MiddlewareRequestId)

    async def manejar_error_api(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, ErrorApi)
        return respuesta_problema(error)

    async def manejar_http(request: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, StarletteHTTPException)
        if error.status_code in (404, 405):
            if autenticador is not None:
                try:
                    await autenticador(request)
                except ErrorApi as fallo:
                    return respuesta_problema(fallo)
            return respuesta_problema(error_generico(404, "La ruta pedida no existe."))
        return respuesta_problema(error_generico(error.status_code, str(error.detail)))

    async def manejar_validacion(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, RequestValidationError)
        problema = error_generico(400, "La petición tiene datos mal formados.")
        problema.errores = [
            ErrorCampo(
                campo=".".join(str(p) for p in detalle.get("loc", ())[1:]) or "cuerpo",
                mensaje=str(detalle.get("msg", "")),
            )
            for detalle in error.errors()
        ]
        return respuesta_problema(problema)

    async def manejar_inesperado(_: Request, error: Exception) -> JSONResponse:
        registro.exception("Error no controlado: %s", type(error).__name__)
        return respuesta_problema(error_generico(500, "Ocurrió un error inesperado."))

    app.add_exception_handler(ErrorApi, manejar_error_api)
    app.add_exception_handler(StarletteHTTPException, manejar_http)
    app.add_exception_handler(RequestValidationError, manejar_validacion)
    app.add_exception_handler(Exception, manejar_inesperado)
