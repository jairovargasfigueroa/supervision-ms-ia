from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.eventos import OBRA_1, OBRA_2, Eventos, cobro_emitido_101, fila, obra_registrada
from ia.comun.tiempo import RelojFijo
from ia.reportes.aplicacion.tareas_programadas import TareasProgramadas
from ia.reportes.infraestructura.programador import crear_programador, programar_reportes
from ia.reportes.infraestructura.repositorio_sql import BaseReportesSql
from ia.reportes.infraestructura.tablas import rpt_cobro, rpt_obra_resumen

pytestmark = pytest.mark.integracion

LA_PAZ = ZoneInfo("America/La_Paz")
COBRO_A = "c2000000-0000-4000-8000-000000000101"
COBRO_B = "c2000000-0000-4000-8000-000000000201"


def _a_las(dia: date, hora: time = time(0, 20)) -> RelojFijo:
    return RelojFijo(datetime.combine(dia, hora, LA_PAZ).astimezone(UTC))


async def test_vencer_cobros_en_el_borde(motor: AsyncEngine) -> None:
    # Al registrarse (2026-10-28) los dos están PENDIENTE.
    eventos = Eventos(motor)
    await eventos.publicar("CobroEmitido", cobro_emitido_101(vence_en="2026-11-09"))
    await eventos.publicar(
        "CobroEmitido",
        {
            "planillaId": "d0000000-0000-4000-8000-000000000201",
            "cobroId": COBRO_B,
            "numero": "COB-2026-0002",
            "venceEn": "2026-11-10",
        },
    )
    tareas = TareasProgramadas(BaseReportesSql(motor), _a_las(date(2026, 11, 10)))
    assert await tareas.vencer_cobros() == 1
    assert (await fila(motor, rpt_cobro, cobro_id=COBRO_A))["estado"] == "VENCIDO"  # venció ayer
    assert (await fila(motor, rpt_cobro, cobro_id=COBRO_B))["estado"] == "PENDIENTE"  # vence hoy
    assert await tareas.vencer_cobros() == 0  # idempotente


async def test_recalcular_plazo_de_todas_las_obras(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)  # D = 2026-10-28
    await eventos.publicar("ObraRegistrada", obra_registrada())  # inicio 2026-06-30, 360 días
    await eventos.publicar("ObraRegistrada", obra_registrada(OBRA_2, fechaInicio="2026-05-31"))  # D − 150
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA_1))["plazo_pct"] == Decimal("33.33")

    tareas = TareasProgramadas(BaseReportesSql(motor), _a_las(date(2026, 11, 17), time(0, 30)))  # D + 20
    assert await tareas.recalcular_plazos() == 2
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA_1))["plazo_pct"] == Decimal("38.89")  # 140 / 360
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA_2))["plazo_pct"] == Decimal("47.22")  # 170 / 360


def test_programa_las_dos_tareas_en_la_paz() -> None:
    programador = crear_programador("America/La_Paz")
    sin_base: Any = None
    programar_reportes(programador, TareasProgramadas(sin_base, _a_las(date(2026, 1, 1))), "America/La_Paz")
    trabajos = {trabajo.id: trabajo for trabajo in programador.get_jobs()}
    assert set(trabajos) == {"vencer_cobros", "recalcular_plazos"}
    for id_, minuto in (("vencer_cobros", "20"), ("recalcular_plazos", "30")):
        disparador = trabajos[id_].trigger
        assert isinstance(disparador, CronTrigger)
        campos = {campo.name: str(campo) for campo in disparador.fields}
        assert campos["hour"] == "0"
        assert campos["minute"] == minuto
        assert str(disparador.timezone) == "America/La_Paz"
