"""Clientes REST de la carga inicial (solo `GET`) y token del cliente confidencial `ia-reportes`.

- Token por *client credentials*; se pide otro cuando falta menos de 30 s para que venza.
- 10 s por llamada; un reintento tras 1 s ante `5xx`, error de red o tiempo agotado; si vuelve a fallar,
  `FalloServicio`.
- Cada corrida manda su `carga_id` como `X-Request-Id` en todas las llamadas.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx

from ia.reportes.aplicacion.lecturas import (
    CobroCargado,
    MovimientoLeido,
    ObraCargada,
    ObraLeida,
    PlanillaCargada,
    PlanillaResumenCargada,
)
from ia.reportes.aplicacion.puertos import FalloServicio
from ia.reportes.infraestructura import campos
from ia.reportes.infraestructura.campos import DatoInvalido
from ia.reportes.infraestructura.eventos import leer_movimiento

TIEMPO_MAXIMO_S = 10.0
ESPERA_REINTENTO_S = 1.0
MARGEN_TOKEN_S = 30.0
TAMANO_PAGINA = 100

Espera = Callable[[float], Awaitable[None]]


class TokenServicio:
    """Token de `ia-reportes` con caché hasta 30 s antes de vencer."""

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
        self._vence: float = 0.0
        self._candado = asyncio.Lock()

    async def token(self) -> str:
        async with self._candado:
            if self._token is not None and self._vence - self._reloj() > MARGEN_TOKEN_S:
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
                raise FalloServicio("Keycloak", f"no se pudo pedir el token ({type(error).__name__})") from error
            if respuesta.status_code != 200:
                raise FalloServicio("Keycloak", f"respondió {respuesta.status_code} al pedir el token de ia-reportes")
            datos = respuesta.json()
            token = datos.get("access_token")
            if not isinstance(token, str) or not token:
                raise FalloServicio("Keycloak", "la respuesta del token no trae access_token")
            self._token = token
            self._vence = pedido_en + float(datos.get("expires_in", 60))
            return token


class ClienteServicio:
    """`GET` a un servicio con el token de `ia-reportes`, tiempo máximo, un reintento y paginación."""

    def __init__(
        self,
        nombre: str,
        url_base: str,
        http: httpx.AsyncClient,
        token: TokenServicio,
        request_id: str,
        espera: Espera = asyncio.sleep,
    ) -> None:
        self.nombre = nombre
        self._url_base = url_base.rstrip("/")
        self._http = http
        self._token = token
        self._request_id = request_id
        self._espera = espera

    async def get(self, ruta: str, params: dict[str, Any] | None = None, *, codigo_404_vacio: str | None = None) -> Any:
        """JSON de la respuesta. Con `codigo_404_vacio`, un 404 con ese `codigo` devuelve `None`."""
        causa = ""
        for intento in (1, 2):
            encabezados = {"Authorization": f"Bearer {await self._token.token()}", "X-Request-Id": self._request_id}
            try:
                respuesta = await self._http.get(
                    f"{self._url_base}{ruta}", params=params, headers=encabezados, timeout=TIEMPO_MAXIMO_S
                )
            except httpx.TimeoutException:
                causa = f"no respondió en {TIEMPO_MAXIMO_S:.0f} s a GET {ruta}"
            except httpx.TransportError as error:
                causa = f"error de red en GET {ruta} ({type(error).__name__})"
            else:
                if respuesta.status_code < 400:
                    return respuesta.json()
                if (
                    respuesta.status_code == 404
                    and codigo_404_vacio is not None
                    and _codigo(respuesta) == codigo_404_vacio
                ):
                    return None
                causa = f"respondió {respuesta.status_code} en GET {ruta}"
                if respuesta.status_code < 500:
                    raise FalloServicio(self.nombre, causa)
            if intento == 1:
                await self._espera(ESPERA_REINTENTO_S)
        raise FalloServicio(self.nombre, causa)

    async def get_paginado(
        self, ruta: str, params: dict[str, Any] | None = None, *, codigo_404_vacio: str | None = None
    ) -> list[dict[str, Any]]:
        """Todas las páginas (`{ items, total, pagina, tamano }`, de a 100)."""
        elementos: list[dict[str, Any]] = []
        pagina = 1
        while True:
            datos = await self.get(
                ruta, {**(params or {}), "pagina": pagina, "tamano": TAMANO_PAGINA}, codigo_404_vacio=codigo_404_vacio
            )
            if datos is None:
                return elementos
            try:
                items = campos.lista_de_objetos(datos, "items")
                total = campos.entero(datos, "total")
            except (DatoInvalido, AttributeError) as error:
                raise FalloServicio(self.nombre, f"respuesta paginada inválida en GET {ruta}") from error
            elementos.extend(items)
            if not items or len(elementos) >= total:
                return elementos
            pagina += 1


def _codigo(respuesta: httpx.Response) -> str | None:
    try:
        cuerpo = respuesta.json()
    except ValueError:
        return None
    return cuerpo.get("codigo") if isinstance(cuerpo, dict) else None


def _traducir[T](servicio: str, ruta: str, traductor: Callable[[], T]) -> T:
    try:
        return traductor()
    except (DatoInvalido, AttributeError, TypeError) as error:
        raise FalloServicio(servicio, f"datos inválidos en GET {ruta}: {error}") from error


class FuenteObrasRest:
    def __init__(self, cliente: ClienteServicio) -> None:
        self._c = cliente

    async def listar_obras(self) -> list[str]:
        datos = await self._c.get("/obras")
        if not isinstance(datos, list):
            raise FalloServicio(self._c.nombre, "GET /obras no devolvió una lista")
        return _traducir(self._c.nombre, "/obras", lambda: [campos.uuid_(obra, "id") for obra in datos])

    async def obtener_obra(self, obra_id: str) -> ObraCargada:
        ruta = f"/obras/{obra_id}"
        datos = await self._c.get(ruta)
        return _traducir(self._c.nombre, ruta, lambda: _obra(datos))

    async def listar_planillas(self, obra_id: str) -> list[PlanillaResumenCargada]:
        ruta = f"/obras/{obra_id}/planillas"
        datos = await self._c.get(ruta)
        if not isinstance(datos, list):
            raise FalloServicio(self._c.nombre, f"GET {ruta} no devolvió una lista")
        return _traducir(
            self._c.nombre,
            ruta,
            lambda: [PlanillaResumenCargada(campos.uuid_(p, "id"), campos.texto(p, "estado")) for p in datos],
        )

    async def obtener_planilla(self, planilla_id: str) -> PlanillaCargada:
        ruta = f"/planillas/{planilla_id}"
        datos = await self._c.get(ruta)
        return _traducir(self._c.nombre, ruta, lambda: _planilla(datos))


class FuenteMaterialesRest:
    def __init__(self, cliente: ClienteServicio) -> None:
        self._c = cliente

    async def precios_materiales(self) -> dict[str, Decimal | None]:
        materiales = await self._c.get_paginado("/materiales")
        return _traducir(
            self._c.nombre,
            "/materiales",
            lambda: {campos.texto(m, "id"): campos.decimal_opcional(m, "precioReferencial") for m in materiales},
        )

    async def listar_movimientos(self, obra_id: str) -> list[MovimientoLeido]:
        ruta = f"/obras/{obra_id}/movimientos"
        elementos = await self._c.get_paginado(
            ruta, {"orden": "registradoEn", "direccion": "asc"}, codigo_404_vacio="OBRA_NO_ENCONTRADA"
        )
        return _traducir(
            self._c.nombre,
            ruta,
            lambda: [leer_movimiento({**m, "obraId": obra_id, "precioReferencial": None}, "id") for m in elementos],
        )


class FuenteFinanzasRest:
    def __init__(self, cliente: ClienteServicio) -> None:
        self._c = cliente

    async def listar_cobros(self, obra_id: str) -> list[CobroCargado]:
        elementos = await self._c.get_paginado("/cobros", {"obraId": obra_id})
        return _traducir(self._c.nombre, "/cobros", lambda: [_cobro(c, obra_id) for c in elementos])


def _obra(datos: dict[str, Any]) -> ObraCargada:
    asignaciones = campos.objeto(datos, "asignaciones")
    contrato = campos.objeto(datos, "contrato")
    return ObraCargada(
        obra=ObraLeida(
            obra_id=campos.uuid_(datos, "id"),
            nombre=campos.texto(datos, "nombre"),
            ubicacion=campos.texto_opcional(datos, "ubicacion"),
            zona=campos.texto_opcional(datos, "zona"),
            cliente_id=campos.uuid_opcional(asignaciones, "clienteId"),
            residente_id=campos.uuid_opcional(asignaciones, "residenteId"),
            maestro_id=campos.uuid_opcional(asignaciones, "maestroId"),
            almacenero_id=campos.uuid_opcional(asignaciones, "almaceneroId"),
            fecha_inicio=campos.fecha(datos, "fechaInicio"),
            plazo_dias=campos.entero(contrato, "plazoDias"),
            monto_contrato=campos.decimal(contrato, "monto"),
        ),
        estado=campos.texto(datos, "estado"),
        avance_pct=campos.decimal_opcional(datos, "avancePct"),
    )


def _planilla(datos: dict[str, Any]) -> PlanillaCargada:
    return PlanillaCargada(
        planilla_id=campos.uuid_(datos, "id"),
        obra_id=campos.uuid_(datos, "obraId"),
        estado=campos.texto(datos, "estado"),
        periodo=campos.periodo_opcional(datos, "periodo"),
        neto=campos.decimal_opcional(datos, "neto"),
        montos_lineas=tuple(campos.decimal(linea, "monto") for linea in campos.lista_de_objetos(datos, "lineas")),
        observacion=campos.texto_opcional(datos, "observacion"),
        cobro_id=campos.uuid_opcional(datos, "cobroId"),
    )


def _cobro(datos: dict[str, Any], obra_id: str) -> CobroCargado:
    return CobroCargado(
        cobro_id=campos.uuid_(datos, "id"),
        numero=campos.texto_opcional(datos, "numero"),
        obra_id=campos.uuid_opcional(datos, "obraId") or obra_id,
        planilla_id=campos.uuid_opcional(datos, "planillaId"),
        monto_neto=campos.decimal_opcional(datos, "montoNeto"),
        emitido_en=campos.fecha_opcional(datos, "emitidoEn"),
        vence_en=campos.fecha_opcional(datos, "venceEn"),
        estado=campos.texto_opcional(datos, "estado"),
    )
