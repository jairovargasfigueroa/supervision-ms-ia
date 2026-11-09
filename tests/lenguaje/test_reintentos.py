"""505: `ModeloConReintentos` reintenta solo las fallas pasajeras del modelo de lenguaje."""

from typing import Any

import pytest

from ia.lenguaje.aplicacion.puerto import LlmNoDisponible, LlmTiempoAgotado, SalidaNoValida
from ia.lenguaje.aplicacion.reintentos import ModeloConReintentos
from ia.lenguaje.dominio.mensajes import Mensaje, Opciones, RespuestaModelo, Rol

OPCIONES = Opciones(tiempo_maximo_s=5)


class Guion:
    """Modelo que responde con un guion de resultados o excepciones, y cuenta las llamadas."""

    def __init__(self, *pasos: Any) -> None:
        self._pasos = list(pasos)
        self.llamadas = 0

    @property
    def proveedor(self) -> str:
        return "anthropic"

    @property
    def modelo(self) -> str:
        return "claude-haiku-5-5"

    def _siguiente(self) -> Any:
        self.llamadas += 1
        paso = self._pasos.pop(0)
        if isinstance(paso, Exception):
            raise paso
        return paso

    async def generar_json(self, mensajes: Any, esquema: Any, opciones: Any) -> dict[str, Any]:
        resultado: dict[str, Any] = self._siguiente()
        return resultado

    async def conversar(self, mensajes: Any, herramientas: Any, opciones: Any) -> RespuestaModelo:
        texto: str = self._siguiente()
        return RespuestaModelo(texto=texto, llamadas=(), mensaje=Mensaje(Rol.ASISTENTE, texto))


def _modelo(*pasos: Any) -> tuple[ModeloConReintentos, Guion]:
    guion = Guion(*pasos)
    return ModeloConReintentos(guion, esperas_s=(0, 0)), guion


async def test_reintenta_fallas_pasajeras_y_devuelve_el_resultado() -> None:
    modelo, guion = _modelo(
        LlmNoDisponible("529", reintentable=True), LlmNoDisponible("sin conexión", reintentable=True), {"ok": 1}
    )
    assert await modelo.generar_json([Mensaje.usuario("x")], {}, OPCIONES) == {"ok": 1}
    assert guion.llamadas == 3
    assert (modelo.proveedor, modelo.modelo) == ("anthropic", "claude-haiku-5-5")


async def test_se_rinde_despues_de_dos_reintentos() -> None:
    modelo, guion = _modelo(*[LlmNoDisponible("529", reintentable=True)] * 3)
    with pytest.raises(LlmNoDisponible):
        await modelo.conversar([Mensaje.usuario("x")], [], OPCIONES)
    assert guion.llamadas == 3


@pytest.mark.parametrize("error", [LlmNoDisponible("400"), LlmTiempoAgotado("lento"), SalidaNoValida("no es JSON")])
async def test_no_reintenta_lo_que_no_es_pasajero(error: Exception) -> None:
    modelo, guion = _modelo(error, {"ok": 1})
    with pytest.raises(type(error)):
        await modelo.generar_json([Mensaje.usuario("x")], {}, OPCIONES)
    assert guion.llamadas == 1
