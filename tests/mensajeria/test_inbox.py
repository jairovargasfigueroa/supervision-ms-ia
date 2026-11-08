from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from apoyo.mensajeria import mensaje, sobre
from ia.comun.tiempo import RelojFijo
from ia.mensajeria.inbox import evento_aplicado, manejador_con_inbox
from ia.mensajeria.sobre import Sobre, leer_sobre

pytestmark = pytest.mark.integracion

RELOJ = RelojFijo(datetime(2026, 10, 31, 12, 0, tzinfo=UTC))


async def _contar(motor: AsyncEngine) -> int:
    async with motor.connect() as conexion:
        return int((await conexion.execute(select(func.count()).select_from(evento_aplicado))).scalar_one())


async def test_mismo_evento_dos_veces_se_aplica_una_sola_vez(motor: AsyncEngine) -> None:
    aplicaciones: list[str] = []

    async def aplicar(sobre_: Sobre, _: AsyncConnection) -> None:
        aplicaciones.append(sobre_.id)

    manejar = manejador_con_inbox(motor, aplicar, RELOJ)
    leido = leer_sobre(mensaje(sobre("ObraRegistrada", {}), "x").cuerpo)
    assert await manejar(leido) is True
    assert await manejar(leido) is False
    assert aplicaciones == [leido.id]
    assert await _contar(motor) == 1


async def test_si_la_proyeccion_falla_el_inbox_se_deshace(motor: AsyncEngine) -> None:
    async def aplicar(_: Sobre, __: AsyncConnection) -> None:
        raise RuntimeError("falla a mitad")

    manejar = manejador_con_inbox(motor, aplicar, RELOJ)
    leido = leer_sobre(mensaje(sobre("ObraRegistrada", {}), "x").cuerpo)
    with pytest.raises(RuntimeError):
        await manejar(leido)
    assert await _contar(motor) == 0
