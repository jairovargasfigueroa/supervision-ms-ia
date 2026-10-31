"""Carga inicial con PostgreSQL real y Obras, Materiales, Finanzas y Keycloak simulados (`httpx.MockTransport`)."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.eventos import AHORA, Eventos, fila, filas, obra_registrada
from apoyo.servicios_011 import (
    FINANZAS,
    ISSUER,
    MATERIAL,
    MATERIALES,
    MOVIMIENTOS_011,
    OBRA,
    OBRAS,
    OBSERVACION_202,
    PLANILLAS_011,
    PRECIO,
    Servicios011,
    cobro_id,
    movimiento_id,
    partida,
    planilla_id,
)
from ia.carga_inicial import principal
from ia.comun.tiempo import RelojFijo
from ia.config import ConfiguracionCarga
from ia.reportes.aplicacion.carga_inicial import CargaEnCurso, CargaInicial, ResultadoCarga
from ia.reportes.aplicacion.lecturas import ObraLeida
from ia.reportes.infraestructura.clientes_rest import (
    ClienteServicio,
    FuenteFinanzasRest,
    FuenteMaterialesRest,
    FuenteObrasRest,
    TokenServicio,
)
from ia.reportes.infraestructura.repositorio_sql import BaseReportesSql, RepositorioReportesSql
from ia.reportes.infraestructura.tablas import (
    carga_inicial,
    rpt_cobro,
    rpt_consumo_material,
    rpt_movimiento,
    rpt_obra_resumen,
    rpt_planilla,
)

pytestmark = pytest.mark.integracion

RELOJ = RelojFijo(AHORA)
TABLAS = (rpt_obra_resumen, rpt_planilla, rpt_consumo_material, rpt_movimiento, rpt_cobro)


class Esperas:
    def __init__(self) -> None:
        self.segundos: list[float] = []

    async def __call__(self, segundos: float) -> None:
        self.segundos.append(segundos)


@asynccontextmanager
async def _carga(
    motor: AsyncEngine, servicios: Servicios011, base: BaseReportesSql | None = None, esperas: Esperas | None = None
) -> AsyncIterator[CargaInicial]:
    async with httpx.AsyncClient(transport=servicios.transporte()) as http:
        token = TokenServicio(http, f"{ISSUER}/protocol/openid-connect/token", "ia-reportes", "secreto")

        def cliente(nombre: str, url: str) -> ClienteServicio:
            return ClienteServicio(nombre, url, http, token, request_id="carga-prueba", espera=esperas or Esperas())

        yield CargaInicial(
            base or BaseReportesSql(motor),
            FuenteObrasRest(cliente("Obras", OBRAS)),
            FuenteMaterialesRest(cliente("Materiales", MATERIALES)),
            FuenteFinanzasRest(cliente("Finanzas", FINANZAS)),
            RELOJ,
        )


async def _correr(motor: AsyncEngine, servicios: Servicios011, carga_id: str, **opciones: Any) -> ResultadoCarga:
    async with _carga(motor, servicios, **opciones) as carga:
        return await carga.ejecutar(carga_id)


def _config(url: str) -> ConfiguracionCarga:
    return ConfiguracionCarga(
        reportes_db_url=url,
        keycloak_issuer=ISSUER,
        obras_url=OBRAS,
        materiales_url=MATERIALES,
        finanzas_url=FINANZAS,
        reportes_client_secret="secreto",  # type: ignore[arg-type]
    )


async def _foto(motor: AsyncEngine) -> dict[str, list[dict[str, Any]]]:
    """Contenido de las tablas rpt_* sin las marcas de tiempo de actualización."""
    resultado: dict[str, list[dict[str, Any]]] = {}
    for tabla in TABLAS:
        filas_tabla = [{k: v for k, v in f.items() if k != "actualizado_en"} for f in await filas(motor, tabla)]
        resultado[tabla.name] = sorted(filas_tabla, key=lambda f: tuple(str(v) for v in f.values()))
    return resultado


def _consumo_esperado() -> dict[tuple[str, str, str, str], tuple[Decimal, Decimal, int]]:
    esperado: dict[tuple[str, str, str, str], tuple[Decimal, Decimal, int]] = {}
    for obra, movimientos in MOVIMIENTOS_011.items():
        for m in movimientos:
            if m["tipo"] != "SALIDA":
                continue
            clave = (OBRA[obra], m["periodo"], m["materialId"], m["partidaId"])
            cantidad, monto, salidas = esperado.get(clave, (Decimal(0), Decimal(0), 0))
            c = Decimal(str(m["cantidad"]))
            p = Decimal(str(PRECIO[m["materialId"]]))
            esperado[clave] = (cantidad + c, monto + (c * p).quantize(Decimal("0.01")), salidas + 1)
    return esperado


async def test_carga_los_datos_de_la_011_y_el_avance_coincide_con_obras(url_migrada: str, motor: AsyncEngine) -> None:
    servicios = Servicios011()
    codigo = await principal(
        _config(url_migrada),
        transporte=servicios.transporte(),
        reloj=RELOJ,
        espera=Esperas(),
        carga_id="0f000000-0000-4000-8000-000000000001",
    )
    assert codigo == 0

    obras = {str(f["obra_id"]): f for f in await filas(motor, rpt_obra_resumen)}
    assert len(obras) == 3
    for numero, avance, plazo, zona in (
        (1, "28.49", "33.33", "NORTE"),
        (2, "16.34", "50.00", "ESTE"),
        (3, "0.00", "2.08", "SUR"),
    ):
        obra = obras[OBRA[numero]]
        assert obra["estado"] == "EN_EJECUCION"
        assert obra["avance_pct"] == Decimal(avance)
        assert obra["plazo_pct"] == Decimal(plazo)
        assert obra["zona"] == zona
    assert str(obras[OBRA[3]]["residente_id"]) == "a0000000-0000-4000-8000-000000000009"

    # 5 planillas en Obras: la PRESENTADA sin observación (obra 1, N.º 3) no se carga (no llega ningún evento por ella).
    planillas = {str(f["planilla_id"]): f for f in await filas(motor, rpt_planilla)}
    assert set(planillas) == {planilla_id(1, 1), planilla_id(1, 2), planilla_id(2, 1), planilla_id(2, 2)}
    aprobada = planillas[planilla_id(1, 1)]
    assert (aprobada["estado"], aprobada["monto_periodo"], aprobada["avance_periodo_pct"]) == (
        "APROBADA",
        Decimal("349560.00"),
        Decimal("12.60"),
    )
    assert aprobada["aprobada_en"] is None
    assert aprobada["cargada_en"] == AHORA
    assert aprobada["veces_observada"] == 0
    observada = planillas[planilla_id(2, 2)]
    assert observada["estado"] == "OBSERVADA"
    assert observada["veces_observada"] == 1
    assert observada["ultimo_motivo"] == OBSERVACION_202
    assert observada["ultimo_origen"] is None

    cobros = {str(f["cobro_id"]): f for f in await filas(motor, rpt_cobro)}
    assert len(cobros) == 3
    vencido = cobros[cobro_id(1, 1)]
    assert vencido["numero"] == "COB-2026-0001"
    assert vencido["estado"] == "VENCIDO"
    assert vencido["monto"] == Decimal("290134.80")
    assert (vencido["emitido_en"], vencido["vence_en"]) == (date(2026, 9, 18), date(2026, 10, 18))
    assert str(vencido["cliente_id"]) == "a0000000-0000-4000-8000-000000000007"
    assert vencido["zona"] == "NORTE"
    assert cobros[cobro_id(2, 1)]["estado"] == "PENDIENTE"
    assert cobros[cobro_id(2, 1)]["zona"] == "ESTE"

    movimientos = await filas(motor, rpt_movimiento)
    assert len(movimientos) == 67
    supera = await fila(motor, rpt_movimiento, movimiento_id=movimiento_id(20018))
    assert supera["supera_stock"] is True
    assert supera["precio_referencial"] == Decimal("62.00")  # precio actual del catálogo

    consumo = {
        (str(f["obra_id"]), f["periodo"], f["material_id"], str(f["partida_id"])): (
            f["cantidad"],
            f["monto"],
            f["salidas"],
        )
        for f in await filas(motor, rpt_consumo_material)
    }
    assert consumo == _consumo_esperado()
    assert consumo[(OBRA[1], "2026-09", MATERIAL["FIE-12"], partida(1, 3))] == (
        Decimal("6000.000"),
        Decimal("51600.00"),
        1,
    )
    assert consumo[(OBRA[2], "2026-09", MATERIAL["CEM-IP30"], partida(2, 4))] == (
        Decimal("310.000"),
        Decimal("19220.00"),
        2,
    )

    corrida = await fila(motor, carga_inicial)
    assert corrida["estado"] == "TERMINADA"
    assert corrida["obras"] == 3
    assert corrida["filas"] == {"rptObraResumen": 3, "rptPlanilla": 4, "rptMovimiento": 67, "rptCobro": 3}
    assert corrida["error"] is None
    assert corrida["terminada_en"] is not None

    # Solo GET a los servicios, con el token de ia-reportes y el X-Request-Id de la corrida.
    a_servicios = servicios.peticiones_a_servicios()
    assert a_servicios
    assert {p.metodo for p in a_servicios} == {"GET"}
    assert {p.encabezados["x-request-id"] for p in a_servicios} == {"0f000000-0000-4000-8000-000000000001"}
    assert servicios.tokens_emitidos == 1


async def test_correrla_dos_veces_deja_las_mismas_filas(motor: AsyncEngine) -> None:
    primera = await _correr(motor, Servicios011(), "0f000000-0000-4000-8000-000000000001")
    foto = await _foto(motor)
    segunda = await _correr(motor, Servicios011(), "0f000000-0000-4000-8000-000000000002")
    assert primera.filas == {"rptObraResumen": 3, "rptPlanilla": 4, "rptMovimiento": 67, "rptCobro": 3}
    assert segunda.estado == "TERMINADA"
    assert segunda.filas == {"rptObraResumen": 0, "rptPlanilla": 0, "rptMovimiento": 0, "rptCobro": 0}
    assert await _foto(motor) == foto
    assert len(await filas(motor, carga_inicial)) == 2


async def test_eventos_viejos_despues_de_la_carga_no_suman(motor: AsyncEngine) -> None:
    await _correr(motor, Servicios011(), "0f000000-0000-4000-8000-000000000001")
    foto = await _foto(motor)
    eventos = Eventos(motor)
    salida_10006 = next(m for m in MOVIMIENTOS_011[1] if m["id"] == movimiento_id(10006))
    datos_movimiento = {
        **{k: v for k, v in salida_10006.items() if k not in ("id", "cerrado", "registradoPor")},
        "movimientoId": salida_10006["id"],
        "obraId": OBRA[1],
        "precioReferencial": 62.00,
    }
    await eventos.publicar("MovimientoRegistrado", datos_movimiento)
    observacion = {"planillaId": planilla_id(2, 2), "obraId": OBRA[2], "motivo": OBSERVACION_202, "origen": "CLIENTE"}
    await eventos.publicar("PlanillaObservada", observacion, ocurrido_en="2026-10-20T15:00:00Z")
    vieja = obra_registrada(OBRA[1])
    del vieja["zona"]
    await eventos.publicar("ObraRegistrada", vieja)
    assert eventos.canal.publicados == []
    assert await _foto(motor) == foto

    # Una observación posterior a la carga sí suma.
    await eventos.publicar(
        "PlanillaObservada", {**observacion, "motivo": "Otra vez."}, ocurrido_en="2026-10-28T17:00:00Z"
    )
    nueva = await fila(motor, rpt_planilla, planilla_id=planilla_id(2, 2))
    assert (nueva["veces_observada"], nueva["ultimo_motivo"], nueva["ultimo_origen"]) == (2, "Otra vez.", "CLIENTE")


class BaseConPausa(BaseReportesSql):
    """Detiene la transacción de la carga de una obra con la fila ya bloqueada, hasta que la prueba la suelte."""

    def __init__(self, motor: AsyncEngine, obra_id: str) -> None:
        super().__init__(motor)
        self.obra_id = obra_id
        self.bloqueada = asyncio.Event()
        self.seguir = asyncio.Event()

    @asynccontextmanager
    async def transaccion(self) -> AsyncIterator[RepositorioReportesSql]:
        async with super().transaccion() as repo:
            original = repo.guardar_obra

            async def guardar_obra(obra: ObraLeida, plazo_pct: Decimal, ahora: datetime) -> bool:
                insertada = await original(obra, plazo_pct, ahora)
                if obra.obra_id == self.obra_id:
                    self.bloqueada.set()
                    await self.seguir.wait()
                return insertada

            repo.guardar_obra = guardar_obra  # type: ignore[method-assign]
            yield repo


async def test_planilla_aprobada_en_paralelo_con_la_carga_de_la_obra_deja_el_avance_correcto(
    motor: AsyncEngine,
) -> None:
    # Obras todavía muestra la N.º 2 de la obra 1 como PRESENTADA; su PlanillaAprobada llega mientras se carga.
    servicios = Servicios011()
    servicios.planillas = [
        (o, n, p, "PRESENTADA" if (o, n) == (1, 2) else e, lineas, neto, con_cobro and (o, n) != (1, 2), obs)
        for (o, n, p, e, lineas, neto, con_cobro, obs) in PLANILLAS_011
    ]
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada(OBRA[1]))
    base = BaseConPausa(motor, OBRA[1])
    aprobada_102 = {
        "planillaId": planilla_id(1, 2),
        "obraId": OBRA[1],
        "periodo": "2026-08",
        "neto": 365764.40,
        "cobroId": cobro_id(1, 2),
        "lineas": [{"monto": 146880.00}, {"monto": 168250.00}, {"monto": 125550.00}],
    }

    async with _carga(motor, servicios, base=base) as carga:
        tarea_carga = asyncio.create_task(carga.ejecutar("0f000000-0000-4000-8000-000000000001"))
        await asyncio.wait_for(base.bloqueada.wait(), 10)
        tarea_evento = asyncio.create_task(eventos.publicar("PlanillaAprobada", aprobada_102))
        await asyncio.sleep(0.3)
        assert not tarea_evento.done()  # espera el bloqueo de la fila de la obra (otra conexión)
        base.seguir.set()
        resultado = await asyncio.wait_for(tarea_carga, 10)
        await asyncio.wait_for(tarea_evento, 10)

    assert resultado.estado == "TERMINADA"
    assert eventos.canal.publicados == []
    obra = await fila(motor, rpt_obra_resumen, obra_id=OBRA[1])
    assert obra["avance_pct"] == Decimal("28.49")
    planilla = await fila(motor, rpt_planilla, planilla_id=planilla_id(1, 2))
    assert planilla["estado"] == "APROBADA"
    assert planilla["aprobada_en"] is not None


async def test_una_segunda_carga_simultanea_se_niega(url_migrada: str, motor: AsyncEngine) -> None:
    async with BaseReportesSql(motor).bloqueo_carga() as tomado:
        assert tomado
        with pytest.raises(CargaEnCurso, match="Ya hay una carga inicial en curso"):
            await _correr(motor, Servicios011(), "0f000000-0000-4000-8000-000000000002")
        codigo = await principal(
            _config(url_migrada), transporte=Servicios011().transporte(), reloj=RELOJ, espera=Esperas()
        )
        assert codigo == 1
    assert await filas(motor, carga_inicial) == []


async def test_un_503_de_finanzas_en_la_segunda_obra_deja_fallida_y_la_siguiente_completa(motor: AsyncEngine) -> None:
    servicios = Servicios011(cobros_con_fallo={OBRA[2]})  # solo falla la obra 2: la obra 1 se carga
    esperas = Esperas()
    resultado = await _correr(motor, servicios, "0f000000-0000-4000-8000-000000000001", esperas=esperas)

    assert resultado.estado == "FALLIDA"
    assert resultado.obras == 1
    assert resultado.error is not None
    assert "Finanzas" in resultado.error
    assert OBRA[2] in resultado.error
    assert "503" in resultado.error
    assert servicios.contar("GET", f"/cobros?obraId={OBRA[2]}") == 2  # un reintento
    assert esperas.segundos == [1.0]
    corrida = await fila(motor, carga_inicial)
    assert corrida["estado"] == "FALLIDA"
    assert corrida["error"] == resultado.error
    assert corrida["filas"]["rptObraResumen"] == 1
    assert {str(f["obra_id"]) for f in await filas(motor, rpt_obra_resumen)} == {OBRA[1]}

    siguiente = await _correr(motor, Servicios011(), "0f000000-0000-4000-8000-000000000002")
    assert siguiente.estado == "TERMINADA"
    assert siguiente.filas == {"rptObraResumen": 2, "rptPlanilla": 2, "rptMovimiento": 24, "rptCobro": 1}
    assert len(await filas(motor, rpt_obra_resumen)) == 3
    assert len(await filas(motor, rpt_movimiento)) == 67
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA[2]))["avance_pct"] == Decimal("16.34")


async def test_corrida_cortada_queda_fallida_al_empezar_la_siguiente(motor: AsyncEngine) -> None:
    async with BaseReportesSql(motor).transaccion() as repo:
        await repo.iniciar_carga("0f000000-0000-4000-8000-0000000000aa", AHORA)
    await _correr(motor, Servicios011(), "0f000000-0000-4000-8000-000000000001")
    cortada = await fila(motor, carga_inicial, carga_id="0f000000-0000-4000-8000-0000000000aa")
    assert cortada["estado"] == "FALLIDA"
    assert cortada["terminada_en"] is not None
    assert (await fila(motor, carga_inicial, carga_id="0f000000-0000-4000-8000-000000000001"))["estado"] == "TERMINADA"


async def test_obra_sin_acceso_en_materiales_es_sin_movimientos(motor: AsyncEngine) -> None:
    servicios = Servicios011(sin_acceso_materiales={OBRA[3]})
    resultado = await _correr(motor, servicios, "0f000000-0000-4000-8000-000000000001")
    assert resultado.estado == "TERMINADA"
    assert resultado.filas["rptMovimiento"] == 65


async def test_pide_un_token_nuevo_cuando_el_anterior_esta_por_vencer() -> None:
    servicios = Servicios011(expira_en=60)
    ahora = {"t": 1000.0}
    async with httpx.AsyncClient(transport=servicios.transporte()) as http:
        token = TokenServicio(
            http, f"{ISSUER}/protocol/openid-connect/token", "ia-reportes", "secreto", lambda: ahora["t"]
        )
        assert await token.token() == "token-1"
        ahora["t"] += 29  # faltan 31 s: sirve
        assert await token.token() == "token-1"
        ahora["t"] += 2  # faltan 29 s: se pide otro
        assert await token.token() == "token-2"
    assert servicios.contar("POST", "/protocol/openid-connect/token") == 2


async def test_pagina_hasta_traer_todo() -> None:
    paginas: list[int] = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        if peticion.method == "POST":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        pagina = int(peticion.url.params["pagina"])
        assert peticion.url.params["tamano"] == "100"
        paginas.append(pagina)
        items = [{"id": str(i)} for i in range((pagina - 1) * 10, min(pagina * 10, 25))]
        return httpx.Response(200, json={"items": items, "total": 25, "pagina": pagina, "tamano": 10})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as http:
        token = TokenServicio(http, "http://kc/token", "ia-reportes", "s")
        cliente = ClienteServicio("Materiales", "http://materiales", http, token, "id", espera=Esperas())
        elementos = await cliente.get_paginado("/materiales")
    assert len(elementos) == 25
    assert paginas == [1, 2, 3]


async def test_red_caida_reintenta_una_vez_y_falla_con_el_servicio() -> None:
    intentos = {"n": 0}

    def responder(peticion: httpx.Request) -> httpx.Response:
        if peticion.method == "POST":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 300})
        intentos["n"] += 1
        raise httpx.ConnectError("conexión rechazada", request=peticion)

    esperas = Esperas()
    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as http:
        token = TokenServicio(http, "http://kc/token", "ia-reportes", "s")
        cliente = ClienteServicio("Obras", "http://obras", http, token, "id", espera=esperas)
        from ia.reportes.aplicacion.puertos import FalloServicio

        with pytest.raises(FalloServicio) as error:
            await cliente.get("/obras")
    assert intentos["n"] == 2
    assert esperas.segundos == [1.0]
    assert error.value.servicio == "Obras"


async def test_sin_secreto_no_corre(url_migrada: str) -> None:
    config = _config(url_migrada)
    config.reportes_client_secret = SecretStr("")
    assert await principal(config, transporte=Servicios011().transporte(), reloj=RELOJ) == 1
