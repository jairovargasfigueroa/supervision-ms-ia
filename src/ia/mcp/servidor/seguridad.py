"""`SeguridadMcp`: delante del servidor MCP, antes de que el SDK lea la petición (501).

Token válido (el mismo validador de la 401: firma, vencimiento, emisor y audiencia) o `401`; rol `agente_ia` (roles
efectivos, `azp = ia-agentes`) o `403`. Responde Problem Details como el resto del servicio. Excepción a la regla
«solo `admin`, `gerente` y `residente`»: `/mcp` acepta **solo** `agente_ia` y las rutas REST siguen sin aceptarlo.
Solo se atiende `POST` (sin estado y sin flujo SSE): otro método responde `405`.
"""

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from ia.auth.dependencias import autenticar_token
from ia.comun.errores import ErrorApi, error_generico
from ia.comun.http import respuesta_problema

ROL_AGENTE = "agente_ia"


class SeguridadMcp:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            usuario = await autenticar_token(Request(scope))
            if ROL_AGENTE not in usuario.roles:
                raise error_generico(403, "El servidor MCP solo atiende al servicio de IA (rol agente_ia).")
        except ErrorApi as error:
            await respuesta_problema(error)(scope, receive, send)
            return
        if scope.get("method") != "POST":
            # Sin estado y sin flujo SSE: el SDK abriría un flujo con GET; aquí solo se atiende POST.
            await Response(status_code=405, headers={"Allow": "POST"})(scope, receive, send)
            return
        await self.app(scope, receive, send)
