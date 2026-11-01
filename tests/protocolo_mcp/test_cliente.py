"""`ClienteMcp` (501) contra un servidor MCP de prueba hecho con el mismo SDK: encabezados, resultados, token en caché
y renovado tras un `401`, servidor caído, servidor lento y error del protocolo."""

from collections.abc import AsyncIterator

import httpx
import pytest

from dobles.servidor_mcp import HerramientaPrueba, ServidorPrueba, esquema_objeto
from ia.mcp.cliente.cliente import ClienteMcp, FalloMcp
from ia.mcp.cliente.token import TokenAgentes
from protocolo_mcp.test_token_agentes import URL, KeycloakSimulado

EJECUCION = "c0000000-0000-4000-8000-000000000001"
OBRA = "b0000000-0000-4000-8000-000000000001"


def _servidor() -> ServidorPrueba:
    return ServidorPrueba(
        "obras",
        [
            HerramientaPrueba(
                "avance_obra",
                esquema_objeto({"obraId": {"type": "string"}, "periodo": {"type": "string"}}),
                lambda argumentos: {"obras": [{"obraId": argumentos.get("obraId", OBRA)}], "truncado": False},
            ),
            HerramientaPrueba(
                "buscar_diario",
                esquema_objeto({"obraId": {"type": "string"}}, ["obraId"]),
                error=("OBRA_NO_ENCONTRADA", "La obra no existe."),
            ),
        ],
    )


@pytest.fixture
async def servidor() -> AsyncIterator[ServidorPrueba]:
    async with _servidor().en_marcha() as en_marcha:
        yield en_marcha


@pytest.fixture
def keycloak() -> KeycloakSimulado:
    return KeycloakSimulado()


def _cliente(servidor: ServidorPrueba, keycloak: KeycloakSimulado, **opciones: float) -> ClienteMcp:
    token = TokenAgentes(httpx.AsyncClient(transport=httpx.MockTransport(keycloak)), URL, "ia-agentes", "s")
    return ClienteMcp("obras", servidor.url, token, EJECUCION, transporte=servidor.transporte(), **opciones)


async def test_camino_feliz_con_encabezados_y_token_en_cache(
    servidor: ServidorPrueba, keycloak: KeycloakSimulado
) -> None:
    cliente = _cliente(servidor, keycloak)
    herramientas = await cliente.listar_herramientas()
    assert [h.nombre for h in herramientas] == ["avance_obra", "buscar_diario"]
    assert herramientas[0].esquema_entrada["additionalProperties"] is False
    assert herramientas[0].solo_lectura is True
    resultado = await cliente.llamar("avance_obra", {"obraId": OBRA})
    assert not resultado.es_error
    assert resultado.datos == {"obras": [{"obraId": OBRA}], "truncado": False}
    assert servidor.llamadas == [("avance_obra", {"obraId": OBRA})]
    assert all(e["authorization"] == "Bearer t1" for e in servidor.encabezados)
    assert all(e["x-request-id"] == EJECUCION for e in servidor.encabezados)
    assert len(keycloak.pedidos) == 1  # el mismo token para todas las llamadas


async def test_error_de_la_herramienta_vuelve_como_resultado(
    servidor: ServidorPrueba, keycloak: KeycloakSimulado
) -> None:
    resultado = await _cliente(servidor, keycloak).llamar("buscar_diario", {"obraId": OBRA})
    assert resultado.es_error
    assert (resultado.codigo, resultado.mensaje) == ("OBRA_NO_ENCONTRADA", "La obra no existe.")


async def test_token_rechazado_se_renueva_y_reintenta_una_vez(
    servidor: ServidorPrueba, keycloak: KeycloakSimulado
) -> None:
    servidor.tokens_rechazados = {"t1"}
    resultado = await _cliente(servidor, keycloak).llamar("avance_obra", {})
    assert not resultado.es_error
    assert len(keycloak.pedidos) == 2
    assert servidor.encabezados[-1]["authorization"] == "Bearer t2"


async def test_token_rechazado_dos_veces_es_fallo(servidor: ServidorPrueba, keycloak: KeycloakSimulado) -> None:
    servidor.tokens_rechazados = {"t1", "t2"}
    with pytest.raises(FalloMcp, match="rechazó el token"):
        await _cliente(servidor, keycloak).llamar("avance_obra", {})


async def test_servidor_caido(servidor: ServidorPrueba, keycloak: KeycloakSimulado) -> None:
    servidor.caido = True
    with pytest.raises(FalloMcp, match="no está disponible"):
        await _cliente(servidor, keycloak).listar_herramientas()


async def test_servidor_sin_red(keycloak: KeycloakSimulado) -> None:
    token = TokenAgentes(httpx.AsyncClient(transport=httpx.MockTransport(keycloak)), URL, "ia-agentes", "s")
    cliente = ClienteMcp("finanzas", "http://127.0.0.1:1/mcp", token, EJECUCION)
    with pytest.raises(FalloMcp) as error:
        await cliente.listar_herramientas()
    assert error.value.tiempo_agotado is False


async def test_servidor_lento_cuenta_como_fallida(servidor: ServidorPrueba, keycloak: KeycloakSimulado) -> None:
    servidor.demora_s = 2.0
    with pytest.raises(FalloMcp) as error:
        await _cliente(servidor, keycloak, tiempo_maximo_s=0.3).llamar("avance_obra", {})
    assert error.value.tiempo_agotado is True


async def test_tiempo_maximo_por_defecto_10_s(servidor: ServidorPrueba, keycloak: KeycloakSimulado) -> None:
    assert _cliente(servidor, keycloak)._tiempo_maximo_s == 10.0


async def test_herramienta_que_no_existe_es_fallo_del_protocolo(
    servidor: ServidorPrueba, keycloak: KeycloakSimulado
) -> None:
    with pytest.raises(FalloMcp, match="protocolo"):
        await _cliente(servidor, keycloak).llamar("borrar_obra", {})


async def test_cierra_su_cliente_http_en_cada_operacion(
    servidor: ServidorPrueba, keycloak: KeycloakSimulado, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El SDK no cierra el `httpx2.AsyncClient` que se le pasa: lo cierra el `ClienteMcp` (también si falla)."""
    import httpx2

    import ia.mcp.cliente.cliente as modulo

    creados: list[httpx2.AsyncClient] = []

    class Registrado(httpx2.AsyncClient):
        def __init__(self, *args: object, **opciones: object) -> None:
            super().__init__(*args, **opciones)  # type: ignore[arg-type]
            creados.append(self)

    monkeypatch.setattr(modulo.httpx2, "AsyncClient", Registrado)
    cliente = _cliente(servidor, keycloak)
    await cliente.listar_herramientas()
    await cliente.llamar("avance_obra", {})
    with pytest.raises(FalloMcp):
        await cliente.llamar("borrar_obra", {})
    assert len(creados) == 3
    assert all(c.is_closed for c in creados)
