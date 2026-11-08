from unittest.mock import AsyncMock, MagicMock

import pytest

from apoyo.mensajeria import CanalSimulado, mensaje, sobre
from ia.mensajeria.consumidor import ConsumidorMensajes
from ia.mensajeria.rabbit import preparar_topologia
from ia.mensajeria.sobre import MensajeInvalidoError, Sobre, leer_sobre

CLAVE = "evento.obras.ObraRegistrada"


class ManejadorFalso:
    def __init__(self, fallos: int = 0, error: Exception | None = None, aplicado: bool = True) -> None:
        self.fallos = fallos
        self.error = error or RuntimeError("base caída")
        self.aplicado = aplicado
        self.llamadas: list[Sobre] = []

    async def __call__(self, sobre_: Sobre) -> bool:
        self.llamadas.append(sobre_)
        if len(self.llamadas) <= self.fallos:
            raise self.error
        return self.aplicado


def _consumidor(manejador: ManejadorFalso) -> ConsumidorMensajes:
    consumidor = ConsumidorMensajes()
    consumidor.registrar(CLAVE, manejador)
    return consumidor


async def test_aplicado_se_confirma_sin_publicar() -> None:
    manejador = ManejadorFalso()
    canal = CanalSimulado()
    await _consumidor(manejador).procesar(mensaje(sobre("ObraRegistrada", {"obraId": "x"}), CLAVE), canal)
    assert len(manejador.llamadas) == 1
    assert manejador.llamadas[0].datos == {"obraId": "x"}
    assert len(canal.confirmados) == 1
    assert canal.publicados == []


async def test_repetido_se_confirma_sin_publicar() -> None:
    canal = CanalSimulado()
    await _consumidor(ManejadorFalso(aplicado=False)).procesar(mensaje(sobre("ObraRegistrada", {}), CLAVE), canal)
    assert len(canal.confirmados) == 1
    assert canal.publicados == []


@pytest.mark.parametrize(
    "contenido",
    [
        b"no es json",
        sobre("ObraRegistrada", {}, version=2),
        {**sobre("ObraRegistrada", {}), "id": "no-es-uuid"},
        {**sobre("ObraRegistrada", {}), "datos": None},
        {**sobre("ObraRegistrada", {}), "ocurridoEn": "ayer"},
    ],
    ids=["no-json", "version-2", "id-invalido", "sin-datos", "fecha-invalida"],
)
async def test_invalido_va_directo_a_fallidos(contenido: object) -> None:
    manejador = ManejadorFalso()
    canal = CanalSimulado()
    await _consumidor(manejador).procesar(mensaje(contenido, CLAVE), canal)  # type: ignore[arg-type]
    assert manejador.llamadas == []
    assert [p.cola for p in canal.publicados] == ["plataforma.fallidos"]
    encabezados = canal.publicados[0].encabezados
    assert encabezados["x-intentos"] == 0
    assert encabezados["x-cola-origen"] == "ia"
    assert encabezados["x-clave-origen"] == CLAVE
    assert encabezados["x-error"]
    assert len(canal.confirmados) == 1


async def test_tipo_sin_manejador_va_a_fallidos() -> None:
    canal = CanalSimulado()
    await _consumidor(ManejadorFalso()).procesar(
        mensaje(sobre("ObraEliminada", {}), "evento.obras.ObraEliminada"), canal
    )
    assert [p.cola for p in canal.publicados] == ["plataforma.fallidos"]
    assert "ObraEliminada" in canal.publicados[0].encabezados["x-error"]


async def test_datos_incompletos_del_manejador_van_directo_a_fallidos() -> None:
    manejador = ManejadorFalso(fallos=1, error=MensajeInvalidoError("Falta obraId."))
    canal = CanalSimulado()
    await _consumidor(manejador).procesar(mensaje(sobre("ObraRegistrada", {}), CLAVE), canal)
    assert [p.cola for p in canal.publicados] == ["plataforma.fallidos"]
    assert canal.publicados[0].encabezados["x-error"] == "Falta obraId."


async def test_reintenta_dos_veces_y_al_tercer_fallo_va_a_fallidos() -> None:
    manejador = ManejadorFalso(fallos=99)
    consumidor = _consumidor(manejador)
    canal = CanalSimulado()
    original = mensaje(sobre("ObraRegistrada", {}), CLAVE)
    original.encabezados["x-otro"] = "se conserva"

    await consumidor.procesar(original, canal)
    primero = canal.publicados[-1]
    assert primero.cola == "ia"
    assert primero.cuerpo == original.cuerpo
    assert primero.encabezados["x-intentos"] == 1
    assert primero.encabezados["x-clave-origen"] == CLAVE
    assert primero.encabezados["x-error"] == "RuntimeError: base caída"
    assert primero.encabezados["x-otro"] == "se conserva"

    await consumidor.procesar(canal.reentregar(primero), canal)
    segundo = canal.publicados[-1]
    assert segundo.cola == "ia"
    assert segundo.encabezados["x-intentos"] == 2
    assert segundo.encabezados["x-clave-origen"] == CLAVE

    await consumidor.procesar(canal.reentregar(segundo), canal)
    tercero = canal.publicados[-1]
    assert tercero.cola == "plataforma.fallidos"
    assert tercero.encabezados["x-intentos"] == 3
    assert tercero.encabezados["x-cola-origen"] == "ia"
    assert tercero.encabezados["x-clave-origen"] == CLAVE
    assert len(manejador.llamadas) == 3
    assert len(canal.confirmados) == 3


async def test_un_fallo_y_despues_se_aplica() -> None:
    manejador = ManejadorFalso(fallos=1)
    consumidor = _consumidor(manejador)
    canal = CanalSimulado()
    await consumidor.procesar(mensaje(sobre("ObraRegistrada", {}), CLAVE), canal)
    await consumidor.procesar(canal.reentregar(canal.publicados[0]), canal)
    assert len(canal.publicados) == 1
    assert len(canal.confirmados) == 2


async def test_pasos_previos_y_posteriores_en_orden() -> None:
    orden: list[str] = []

    async def manejador(_: Sobre) -> bool:
        orden.append("manejador")
        return True

    async def previo(_: Sobre) -> None:
        orden.append("previo")

    async def posterior(_: Sobre, aplicado: bool) -> None:
        orden.append(f"posterior:{aplicado}")

    consumidor = ConsumidorMensajes()
    consumidor.registrar(CLAVE, manejador)
    consumidor.agregar_previo("ObraRegistrada", previo)
    consumidor.agregar_posterior("ObraRegistrada", posterior)
    await consumidor.procesar(mensaje(sobre("ObraRegistrada", {}), CLAVE), CanalSimulado())
    assert orden == ["previo", "manejador", "posterior:True"]
    assert consumidor.claves() == [CLAVE]
    with pytest.raises(ValueError):
        consumidor.registrar(CLAVE, manejador)
    with pytest.raises(ValueError):
        consumidor.agregar_previo("Otro", previo)


def test_leer_sobre_valido() -> None:
    contenido = sobre("CobroEmitido", {"cobroId": "c"}, ocurrido_en="2026-10-28T14:30:00Z")
    leido = leer_sobre(mensaje(contenido, "x").cuerpo)
    assert leido.tipo == "CobroEmitido"
    assert leido.ocurrido_en is not None and leido.ocurrido_en.isoformat() == "2026-10-28T14:30:00+00:00"
    assert leido.correlacion_id == contenido["correlacionId"]


async def test_topologia_declara_cola_fallidos_y_enlaces() -> None:
    canal = MagicMock()
    exchange = object()
    cola = MagicMock()
    cola.bind = AsyncMock()
    canal.declare_exchange = AsyncMock(return_value=exchange)
    canal.declare_queue = AsyncMock(return_value=cola)
    claves = [CLAVE, "evento.finanzas.CobroEmitido"]
    await preparar_topologia(canal, "ia", claves)
    canal.declare_exchange.assert_awaited_once()
    assert canal.declare_exchange.await_args.args[0] == "plataforma"
    nombres = [llamada.args[0] for llamada in canal.declare_queue.await_args_list]
    assert nombres == ["plataforma.fallidos", "ia"]
    assert all(llamada.kwargs["durable"] for llamada in canal.declare_queue.await_args_list)
    assert [llamada.kwargs["routing_key"] for llamada in cola.bind.await_args_list] == claves
