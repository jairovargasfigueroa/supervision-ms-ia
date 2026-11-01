"""Trabajo de los agentes en `ia-worker` (502): cola una a la vez, ejecución diaria sin repetir en el mismo día,
recuperación al iniciar y bucle de la cola."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from pymongo.asynchronous.database import AsyncDatabase

from ia.agentes.aplicacion.trabajo import TrabajoAgentes
from ia.agentes.dominio.ejecucion import EN_CURSO, FALLIDA, MANUAL, PROGRAMADA, Ejecucion, ErrorEjecucion
from ia.agentes.infraestructura.repositorio_mongo import RepositorioEjecucionesMongo
from ia.agentes.infraestructura.worker import bucle_cola, programar_agentes
from ia.comun.mongo import asegurar_indices
from ia.comun.tiempo import RelojFijo
from ia.reportes.infraestructura.programador import crear_programador

pytestmark = pytest.mark.integracion

AHORA = datetime(2027, 4, 20, 10, 0, tzinfo=UTC)  # 06:00 en La Paz


class EjecutorContador:
    """Doble del ejecutor: registra qué ejecuciones corrió (con una pausa, para que se crucen dos workers)."""

    def __init__(self, repo: RepositorioEjecucionesMongo, reloj: RelojFijo) -> None:
        self.corridas: list[str] = []
        self._repo = repo
        self._reloj = reloj

    async def ejecutar(self, ejecucion: Ejecucion) -> None:
        self.corridas.append(ejecucion.id)
        await asyncio.sleep(0.05)
        await self._repo.terminar(ejecucion.id, "ok", [], self._reloj.ahora())


@pytest.fixture
async def repo(ia_db: AsyncDatabase[dict[str, Any]]) -> RepositorioEjecucionesMongo:
    await asegurar_indices(ia_db)
    return RepositorioEjecucionesMongo(ia_db)


def trabajo(repo: RepositorioEjecucionesMongo, reloj: RelojFijo) -> tuple[TrabajoAgentes, EjecutorContador]:
    ejecutor = EjecutorContador(repo, reloj)
    return TrabajoAgentes(repo, ejecutor, reloj, "ollama", "qwen3:8b"), ejecutor  # type: ignore[arg-type]


async def test_encola_los_tres_agentes_una_vez_por_dia(repo: RepositorioEjecucionesMongo) -> None:
    reloj = RelojFijo(AHORA)
    t, ejecutor = trabajo(repo, reloj)
    assert await t.encolar_diarios() == ["SEGUIMIENTO_OBRAS", "CONTROL_MATERIALES", "CONTROL_COBROS"]
    encoladas = await repo.listar(None, EN_CURSO, 10)
    assert {(e.origen, e.solicitada_por, e.proveedor_llm, e.modelo) for e in encoladas} == {
        (PROGRAMADA, None, "ollama", "qwen3:8b")
    }
    while await t.procesar_siguiente():
        pass
    assert len(ejecutor.corridas) == 3
    reloj.momento = AHORA + timedelta(hours=10)  # mismo día en La Paz
    assert await t.encolar_diarios() == []
    reloj.momento = AHORA + timedelta(days=1)
    assert await t.encolar_diarios() == ["SEGUIMIENTO_OBRAS", "CONTROL_MATERIALES", "CONTROL_COBROS"]


async def test_no_encola_si_el_agente_esta_en_curso(repo: RepositorioEjecucionesMongo) -> None:
    reloj = RelojFijo(AHORA)
    t, _ = trabajo(repo, reloj)
    await repo.crear(Ejecucion.nueva("CONTROL_COBROS", MANUAL, "g1", AHORA, "ollama", "m"))
    assert await t.encolar_diarios() == ["SEGUIMIENTO_OBRAS", "CONTROL_MATERIALES"]


async def test_cola_una_a_la_vez_y_dos_workers_no_toman_la_misma(repo: RepositorioEjecucionesMongo) -> None:
    reloj = RelojFijo(AHORA)
    t1, e1 = trabajo(repo, reloj)
    t2, e2 = trabajo(repo, reloj)
    for minutos, agente in enumerate(("SEGUIMIENTO_OBRAS", "CONTROL_MATERIALES", "CONTROL_COBROS")):
        await repo.crear(Ejecucion.nueva(agente, MANUAL, "g1", AHORA + timedelta(minutes=minutos), "ollama", "m"))

    async def vaciar(t: TrabajoAgentes) -> None:
        while await t.procesar_siguiente():
            pass

    await asyncio.gather(vaciar(t1), vaciar(t2))
    corridas = e1.corridas + e2.corridas
    assert len(corridas) == len(set(corridas)) == 3
    assert not await t1.procesar_siguiente()


async def test_recupera_las_en_curso_de_mas_de_10_minutos(repo: RepositorioEjecucionesMongo) -> None:
    vieja = Ejecucion.nueva("CONTROL_COBROS", MANUAL, "g1", AHORA - timedelta(minutes=11), "ollama", "m")
    reciente = Ejecucion.nueva("CONTROL_MATERIALES", MANUAL, "g1", AHORA - timedelta(minutes=9), "ollama", "m")
    await repo.crear(vieja)
    await repo.crear(reciente)
    t, _ = trabajo(repo, RelojFijo(AHORA))
    assert await t.recuperar() == 1
    recuperada = await repo.obtener(vieja.id)
    assert recuperada is not None
    assert recuperada.estado == FALLIDA
    assert recuperada.error == ErrorEjecucion("INTERRUMPIDA", "La ejecución se interrumpió porque el servicio se reinició.")
    sigue = await repo.obtener(reciente.id)
    assert sigue is not None
    assert sigue.estado == EN_CURSO


async def test_bucle_de_la_cola_procesa_y_se_detiene(repo: RepositorioEjecucionesMongo) -> None:
    reloj = RelojFijo(AHORA)
    t, ejecutor = trabajo(repo, reloj)
    detener = asyncio.Event()
    tarea = asyncio.create_task(bucle_cola(t, detener, espera_s=0.05))
    await repo.crear(Ejecucion.nueva("CONTROL_COBROS", MANUAL, "g1", AHORA, "ollama", "m"))
    for _ in range(100):
        if ejecutor.corridas:
            break
        await asyncio.sleep(0.02)
    detener.set()
    await asyncio.wait_for(tarea, timeout=2)
    assert len(ejecutor.corridas) == 1


async def test_programacion_diaria_con_el_cron_de_la_configuracion(repo: RepositorioEjecucionesMongo) -> None:
    programador = crear_programador("America/La_Paz")
    t, _ = trabajo(repo, RelojFijo(AHORA))
    programar_agentes(programador, t, "0 6 * * *", "America/La_Paz")
    tarea = programador.get_job("agentes_diario")
    assert tarea is not None
    assert isinstance(tarea.trigger, CronTrigger)
    siguiente = tarea.trigger.get_next_fire_time(None, datetime(2027, 4, 20, 0, 0, tzinfo=ZoneInfo("America/La_Paz")))
    assert siguiente == datetime(2027, 4, 20, 6, 0, tzinfo=ZoneInfo("America/La_Paz"))


async def test_al_iniciar_recupera_toda_ejecucion_tomada_y_en_marcha_las_colgadas(
    repo: RepositorioEjecucionesMongo,
) -> None:
    reloj = RelojFijo(AHORA)
    t, _ = trabajo(repo, reloj)
    reciente = Ejecucion.nueva("CONTROL_COBROS", MANUAL, "g1", AHORA - timedelta(minutes=1), "ollama", "m")
    await repo.crear(reciente)
    assert await repo.tomar_siguiente(AHORA - timedelta(seconds=30)) is not None
    # Al arrancar, el único worker que podía tenerla es el que arranca: queda INTERRUMPIDA aunque sea reciente.
    assert await t.recuperar() == 1
    assert await repo.crear(Ejecucion.nueva("CONTROL_COBROS", MANUAL, "g1", AHORA, "ollama", "m")) is None  # sin 409

    # En marcha: una tomada hace más de 10 minutos ya no puede seguir viva (el máximo es 3).
    reloj.momento = AHORA + timedelta(minutes=5)
    assert await repo.tomar_siguiente(AHORA) is not None
    assert await t.recuperar_colgadas() == 0
    reloj.momento = AHORA + timedelta(minutes=11)
    assert await t.recuperar_colgadas() == 1
    assert [e.estado for e in await repo.listar("CONTROL_COBROS", None, 10)] == [FALLIDA, FALLIDA]


async def test_una_ejecucion_cancelada_queda_interrumpida(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    from agentes.apoyo_agentes import entorno
    from dobles.modelo_simulado import ModeloSimulado

    async with entorno(ia_db, ModeloSimulado(conversacion=["Listo."], demora_s=30)) as e:
        ejecucion = await e.crear("CONTROL_COBROS")
        tarea = asyncio.create_task(e.ejecutor.ejecutar(ejecucion))
        await asyncio.sleep(0.5)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        guardada = await e.ejecuciones.obtener(ejecucion.id)
    assert guardada is not None
    assert guardada.estado == FALLIDA
    assert guardada.error is not None
    assert guardada.error.codigo == "INTERRUMPIDA"
