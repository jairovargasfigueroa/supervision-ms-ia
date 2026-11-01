"""Repositorios de `ejecucion_agente` y `sugerencia` (502) con MongoDB real: índices, cola, pasos, recuperación,
sugerencias pendientes sin duplicar y decisión atómica."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase

from ia.agentes.aplicacion.puertos import EjecucionEnCurso
from ia.agentes.dominio.ejecucion import (
    APROBADA,
    EN_CURSO,
    FALLIDA,
    MANUAL,
    PENDIENTE,
    PROGRAMADA,
    TERMINADA,
    Ejecucion,
    ErrorEjecucion,
    Evidencia,
    HallazgoGuardado,
    Paso,
    Sugerencia,
)
from ia.agentes.infraestructura.repositorio_mongo import RepositorioEjecucionesMongo, RepositorioSugerenciasMongo
from ia.comun.mongo import asegurar_indices

pytestmark = pytest.mark.integracion

AHORA = datetime(2027, 4, 20, 10, 0, tzinfo=UTC)
OBRA = "b0000000-0000-4000-8000-000000000001"


def ejecucion(agente: str = "SEGUIMIENTO_OBRAS", minutos: int = 0, origen: str = MANUAL) -> Ejecucion:
    return Ejecucion(
        id=str(uuid.uuid4()),
        agente=agente,
        origen=origen,
        estado=EN_CURSO,
        solicitada_por=None if origen == PROGRAMADA else "a0000000-0000-4000-8000-000000000002",
        iniciada_en=AHORA + timedelta(minutes=minutos),
        proveedor_llm="ollama",
        modelo="qwen3:8b",
    )


def sugerencia(accion: str = "PROGRAMAR_VISITA", obra: str = OBRA, minutos: int = 0) -> Sugerencia:
    return Sugerencia(
        id=str(uuid.uuid4()),
        ejecucion_id="e1",
        agente="SEGUIMIENTO_OBRAS",
        obra_id=obra,
        obra_nombre="Edificio Las Palmas",
        accion=accion,
        descripcion="Visitar la obra.",
        hallazgo_indice=0,
        creada_en=AHORA + timedelta(minutes=minutos),
    )


@pytest.fixture
async def base(ia_db: AsyncDatabase[dict[str, Any]]) -> AsyncDatabase[dict[str, Any]]:
    await asegurar_indices(ia_db)
    return ia_db


async def test_indices_del_diseno(base: AsyncDatabase[dict[str, Any]]) -> None:
    ejecuciones = await base["ejecucion_agente"].index_information()
    sugerencias = await base["sugerencia"].index_information()
    assert {
        "ix_ejecucion_agente_agente_iniciada",
        "ix_ejecucion_agente_estado",
        "ix_ejecucion_agente_hallazgos_obra",
        "ux_ejecucion_agente_en_curso",
    } <= set(ejecuciones)
    assert {"ix_sugerencia_estado_agente", "ix_sugerencia_obra_estado", "ux_sugerencia_pendiente_obra_accion"} <= set(
        sugerencias
    )
    unico = sugerencias["ux_sugerencia_pendiente_obra_accion"]
    assert unico["unique"] is True
    assert unico["partialFilterExpression"] == {"estado": PENDIENTE}


async def test_una_sola_en_curso_por_agente(base: AsyncDatabase[dict[str, Any]]) -> None:
    repo = RepositorioEjecucionesMongo(base)
    await repo.crear(ejecucion())
    with pytest.raises(EjecucionEnCurso):
        await repo.crear(ejecucion())
    await repo.crear(ejecucion("CONTROL_COBROS"))  # otro agente sí


async def test_cola_toma_la_mas_antigua_y_dos_workers_no_toman_la_misma(
    base: AsyncDatabase[dict[str, Any]],
) -> None:
    repo = RepositorioEjecucionesMongo(base)
    vieja, nueva = ejecucion("CONTROL_COBROS", 0), ejecucion("CONTROL_MATERIALES", 5)
    await repo.crear(nueva)
    await repo.crear(vieja)
    primera, segunda, tercera = await asyncio.gather(*(repo.tomar_siguiente(AHORA) for _ in range(3)))
    tomadas = [e.id for e in (primera, segunda, tercera) if e is not None]
    assert sorted(tomadas) == sorted([vieja.id, nueva.id])
    assert len(set(tomadas)) == 2
    assert (await repo.tomar_siguiente(AHORA)) is None
    guardada = await repo.obtener(vieja.id)
    assert guardada is not None
    assert guardada.tomada_en == AHORA


async def test_pasos_terminar_y_fallar(base: AsyncDatabase[dict[str, Any]]) -> None:
    repo = RepositorioEjecucionesMongo(base)
    e = ejecucion()
    await repo.crear(e)
    await repo.agregar_paso(e.id, Paso(1, "obras", "avance_obra", "{}", "2 filas: …", AHORA, 120))
    await repo.agregar_paso(
        e.id, Paso(2, "finanzas", "cobros_por_vencer", "{}", "", AHORA, 10, "SERVIDOR_NO_DISPONIBLE")
    )
    hallazgo = HallazgoGuardado(
        OBRA, "Las Palmas", "ATRASO", "ALTA", "Atrasada", "Lluvia", (Evidencia("avance_obra", "8,5"),)
    )
    await repo.terminar(e.id, "Una obra atrasada.", [hallazgo], AHORA + timedelta(minutes=1))
    terminada = await repo.obtener(e.id)
    assert terminada is not None
    assert terminada.estado == TERMINADA
    assert [p.numero for p in terminada.pasos] == [1, 2]
    assert terminada.pasos[1].error == "SERVIDOR_NO_DISPONIBLE"
    assert terminada.hallazgos == (hallazgo,)
    assert (terminada.proveedor_llm, terminada.modelo) == ("ollama", "qwen3:8b")
    assert (await repo.ultima_terminada("SEGUIMIENTO_OBRAS")) == terminada

    otra = ejecucion(minutos=1)
    await repo.crear(otra)
    await repo.fallar(otra.id, ErrorEjecucion("REPORTE_INVALIDO", "No logró un reporte válido."), AHORA)
    fallida = await repo.obtener(otra.id)
    assert fallida is not None
    assert (fallida.estado, fallida.error) == (
        FALLIDA,
        ErrorEjecucion("REPORTE_INVALIDO", "No logró un reporte válido."),
    )
    assert (await repo.ultima("SEGUIMIENTO_OBRAS")) == fallida
    assert [x.id for x in await repo.listar("SEGUIMIENTO_OBRAS", None, 20)] == [otra.id, e.id]
    assert [x.id for x in await repo.listar(None, FALLIDA, 20)] == [otra.id]


async def test_recuperar_interrumpidas_y_programada_del_dia(base: AsyncDatabase[dict[str, Any]]) -> None:
    repo = RepositorioEjecucionesMongo(base)
    vieja, reciente = ejecucion("CONTROL_COBROS", -15, PROGRAMADA), ejecucion("CONTROL_MATERIALES", -5)
    await repo.crear(vieja)
    await repo.crear(reciente)
    assert await repo.recuperar_interrumpidas(AHORA, iniciadas_antes_de=AHORA - timedelta(minutes=10)) == 1
    recuperada = await repo.obtener(vieja.id)
    assert recuperada is not None
    assert (recuperada.estado, recuperada.error and recuperada.error.codigo) == (FALLIDA, "INTERRUMPIDA")
    assert await repo.hay_programada_desde("CONTROL_COBROS", AHORA - timedelta(hours=1))
    assert not await repo.hay_programada_desde("CONTROL_COBROS", AHORA)
    assert not await repo.hay_programada_desde("CONTROL_MATERIALES", AHORA - timedelta(hours=1))  # es MANUAL


async def test_sugerencia_pendiente_no_se_duplica_y_se_decide_una_vez(base: AsyncDatabase[dict[str, Any]]) -> None:
    repo = RepositorioSugerenciasMongo(base)
    primera = sugerencia()
    assert await repo.crear(primera)
    assert not await repo.crear(sugerencia())  # misma obra y acción pendientes
    assert await repo.crear(sugerencia("SOLICITAR_INFORME_RESIDENTE", minutos=1))
    assert await repo.contar_pendientes("SEGUIMIENTO_OBRAS", None) == 2
    assert await repo.contar_pendientes("SEGUIMIENTO_OBRAS", ["otra"]) == 0

    aprobada = await repo.decidir(primera.id, APROBADA, "g1", AHORA, "Ok")
    assert aprobada is not None
    assert (aprobada.estado, aprobada.decidida_por, aprobada.comentario) == (APROBADA, "g1", "Ok")
    assert await repo.decidir(primera.id, "RECHAZADA", "g1", AHORA, "Motivo largo") is None
    assert await repo.crear(sugerencia())  # ya no hay una pendiente igual

    todas = await repo.listar(None, ["SEGUIMIENTO_OBRAS"], None, 50)
    assert len(todas) == 3
    assert [s.estado for s in await repo.listar(APROBADA, ["SEGUIMIENTO_OBRAS"], [OBRA], 50)] == [APROBADA]
    assert await repo.listar(None, ["CONTROL_COBROS"], None, 50) == []


async def test_recuperar_las_tomadas(base: AsyncDatabase[dict[str, Any]]) -> None:
    """Una ejecución tomada que quedó EN_CURSO (worker cancelado o MongoDB caído al guardar) también se recupera."""
    repo = RepositorioEjecucionesMongo(base)
    tomada, en_espera = ejecucion("CONTROL_COBROS", -1), ejecucion("CONTROL_MATERIALES", -1)
    await repo.crear(tomada)
    await repo.crear(en_espera)
    await base["ejecucion_agente"].update_one({"_id": tomada.id}, {"$set": {"tomada_en": AHORA - timedelta(minutes=1)}})
    assert await repo.recuperar_interrumpidas(AHORA, tomadas_antes_de=AHORA - timedelta(minutes=10)) == 0
    assert await repo.recuperar_interrumpidas(AHORA, tomadas_antes_de=AHORA) == 1
    recuperada, sigue = await repo.obtener(tomada.id), await repo.obtener(en_espera.id)
    assert recuperada is not None
    assert sigue is not None
    assert (recuperada.estado, sigue.estado) == (FALLIDA, EN_CURSO)
