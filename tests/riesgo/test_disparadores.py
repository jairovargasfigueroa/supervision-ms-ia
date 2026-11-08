"""Recálculo del riesgo tras eventos, al terminar la carga inicial y a diario (PostgreSQL y MongoDB reales)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from apscheduler.triggers.cron import CronTrigger
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.eventos import (
    OBRA_1,
    OBRA_2,
    PLANILLA_202,
    Eventos,
    fila,
    movimiento,
    obra_registrada,
    planilla_aprobada_101,
)
from apoyo.servicios_011 import FINANZAS, ISSUER, MATERIALES, OBRA, OBRAS, Servicios011
from ia.carga_inicial import principal
from ia.comun.tiempo import RelojFijo
from ia.config import Configuracion, ConfiguracionCarga
from ia.reportes.infraestructura.programador import crear_programador
from ia.reportes.infraestructura.tablas import rpt_obra_resumen
from ia.riesgo.aplicacion.recalcular import ServicioRiesgo, clave_evento
from ia.riesgo.dominio.variables import nivel
from ia.riesgo.infraestructura.componer import crear_servicio_riesgo
from ia.riesgo.infraestructura.disparadores import programar_riesgo, registrar_disparadores
from ia.riesgo.infraestructura.fuente_sql import ContadorAtipicosMongo, FuenteRiesgoSql
from ia.riesgo.infraestructura.modelo_xgboost import ModeloXgboost, cargar_modelo
from ia.riesgo.infraestructura.predicciones_mongo import AlmacenPrediccionesMongo

pytestmark = pytest.mark.integracion

MODELOS = Path(__file__).resolve().parents[2] / "modelos"
CONTRATO_1 = {"obraId": OBRA_1, "contratoId": "c0000000-0000-4000-8000-000000000001"}


@pytest.fixture(scope="module")
def modelo() -> ModeloXgboost:
    cargado = cargar_modelo(MODELOS)
    assert cargado is not None
    return cargado


def _servicio(
    motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]], eventos: Eventos, modelo: Any
) -> ServicioRiesgo:
    return crear_servicio_riesgo(Configuracion(), motor, ia_db, eventos.reloj, modelo=modelo)


async def _predicciones(ia_db: AsyncDatabase[dict[str, Any]], **filtro: Any) -> list[dict[str, Any]]:
    return [d async for d in ia_db["prediccion"].find(filtro).sort("calculada_en", 1)]


async def test_tras_contrato_y_planilla_aprobada_se_recalcula(
    motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]], modelo: ModeloXgboost
) -> None:
    eventos = Eventos(motor)
    registrar_disparadores(eventos.consumidor, _servicio(motor, ia_db, eventos, modelo))
    await eventos.publicar("ObraRegistrada", obra_registrada())
    id_contrato = await eventos.publicar("ContratoRegistrado", CONTRATO_1)
    id_planilla = await eventos.publicar("PlanillaAprobada", planilla_aprobada_101())
    await eventos.publicar("PlanillaAprobada", planilla_aprobada_101(), id_=id_planilla)  # repetido

    documentos = await _predicciones(ia_db, obra_id=OBRA_1)
    assert [d["motivo"] for d in documentos] == ["CONTRATO_REGISTRADO", "PLANILLA_APROBADA"]
    assert [d["clave"] for d in documentos] == [clave_evento(OBRA_1, id_contrato), clave_evento(OBRA_1, id_planilla)]
    ultima = documentos[-1]
    assert ultima["nivel"] in ("BAJO", "MEDIO", "ALTO")
    assert 0 <= ultima["riesgo_retraso"] <= 1
    assert ultima["version_modelo"] == "rr-1.0.0"
    assert ultima["variables"]["avance_pct"] == 12.6
    assert ultima["variables"]["ritmo_reciente"] == 12.6
    obra = await fila(motor, rpt_obra_resumen, obra_id=OBRA_1)
    assert obra["nivel_riesgo"] == ultima["nivel"]
    assert float(obra["riesgo_retraso"]) == pytest.approx(ultima["riesgo_retraso"], abs=1e-3)
    assert eventos.canal.publicados == []


async def test_obra_sin_contrato_queda_sin_datos(
    motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]], modelo: ModeloXgboost
) -> None:
    eventos = Eventos(motor)
    registrar_disparadores(eventos.consumidor, _servicio(motor, ia_db, eventos, modelo))
    await eventos.publicar("ObraRegistrada", obra_registrada(OBRA_2))
    observacion = {"planillaId": PLANILLA_202, "obraId": OBRA_2, "motivo": "Corrijan.", "origen": "CLIENTE"}
    await eventos.publicar("PlanillaObservada", observacion)
    (documento,) = await _predicciones(ia_db, obra_id=OBRA_2)
    assert (documento["motivo"], documento["nivel"], documento["riesgo_retraso"]) == (
        "PLANILLA_OBSERVADA",
        "SIN_DATOS",
        None,
    )
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA_2))["nivel_riesgo"] == "SIN_DATOS"


async def test_si_el_calculo_falla_el_evento_no_se_reintenta(motor: AsyncEngine, modelo: ModeloXgboost) -> None:
    class Roto:
        async def recalcular_obra(self, *_: Any) -> None:
            raise RuntimeError("MongoDB caído")

    eventos = Eventos(motor)
    registrar_disparadores(eventos.consumidor, Roto())  # type: ignore[arg-type]
    await eventos.publicar("ObraRegistrada", obra_registrada())
    await eventos.publicar("ContratoRegistrado", CONTRATO_1)
    assert eventos.canal.publicados == []
    assert len(eventos.canal.confirmados) == 2
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA_1))["estado"] == "EN_EJECUCION"


async def test_diario_solo_en_ejecucion_e_idempotente_en_el_dia(
    motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]], modelo: ModeloXgboost
) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    await eventos.publicar("ContratoRegistrado", CONTRATO_1)
    await eventos.publicar("ObraRegistrada", obra_registrada(OBRA_2))  # sin contrato
    servicio = _servicio(motor, ia_db, eventos, modelo)
    assert await servicio.diario() == 1
    assert await servicio.diario() == 1
    documentos = await _predicciones(ia_db)
    assert [(d["obra_id"], d["clave"], d["motivo"]) for d in documentos] == [
        (OBRA_1, f"{OBRA_1}:diario:2026-10-28", "DIARIO")
    ]


async def test_sin_modelo_no_calcula(motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    await eventos.publicar("ContratoRegistrado", CONTRATO_1)
    servicio = ServicioRiesgo(
        FuenteRiesgoSql(motor), ContadorAtipicosMongo(ia_db), AlmacenPrediccionesMongo(ia_db), None, eventos.reloj
    )
    assert await servicio.recalcular_obra(OBRA_1, "DIARIO", "x") is None
    assert await servicio.diario() == 0
    assert await _predicciones(ia_db) == []


async def test_cuenta_atipicos_y_salidas_sin_stock_de_los_ultimos_90_dias(
    motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]], modelo: ModeloXgboost
) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    await eventos.publicar("ContratoRegistrado", CONTRATO_1)
    ahora = eventos.reloj.ahora()
    await ia_db["consumo_atipico"].insert_many(
        [
            {
                "_id": "m1",
                "obra_id": OBRA_1,
                "detectado_en": ahora - timedelta(days=10),
                "estado_revision": "PENDIENTE",
            },
            {
                "_id": "m2",
                "obra_id": OBRA_1,
                "detectado_en": ahora - timedelta(days=20),
                "estado_revision": "CONFIRMADO",
            },
            {"_id": "m3", "obra_id": OBRA_1, "detectado_en": ahora - timedelta(days=5), "estado_revision": "NORMAL"},
            {
                "_id": "m4",
                "obra_id": OBRA_1,
                "detectado_en": ahora - timedelta(days=120),
                "estado_revision": "PENDIENTE",
            },
            {"_id": "m5", "obra_id": OBRA_2, "detectado_en": ahora, "estado_revision": "PENDIENTE"},
        ]
    )
    await eventos.publicar("MovimientoRegistrado", movimiento(superaStock=True, registradoEn="2026-10-01T14:00:00Z"))
    await eventos.publicar("MovimientoRegistrado", movimiento(superaStock=True, registradoEn="2026-06-01T14:00:00Z"))
    prediccion = await _servicio(motor, ia_db, eventos, modelo).recalcular_obra(OBRA_1, "CONSUMO_ATIPICO", "k")
    assert prediccion is not None
    assert prediccion.variables["atipicos_90d"] == 2
    assert prediccion.variables["supera_stock_90d"] == 1


async def test_al_terminar_la_carga_inicial_predice_todas_las_obras(
    url_migrada: str, url_mongo_pruebas: str, motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]]
) -> None:
    config = ConfiguracionCarga(
        reportes_db_url=url_migrada,
        mongo_url=url_mongo_pruebas,
        mongo_db=ia_db.name,
        keycloak_issuer=ISSUER,
        obras_url=OBRAS,
        materiales_url=MATERIALES,
        finanzas_url=FINANZAS,
        modelos_dir=str(MODELOS),
        reportes_client_secret="secreto",  # type: ignore[arg-type]
    )

    async def sin_espera(_: float) -> None:
        return None

    carga_id = "0f000000-0000-4000-8000-000000000001"
    reloj = RelojFijo(datetime(2026, 10, 28, 16, 0, tzinfo=UTC))
    codigo = await principal(
        config, transporte=Servicios011().transporte(), reloj=reloj, espera=sin_espera, carga_id=carga_id
    )
    assert codigo == 0
    documentos = await _predicciones(ia_db, motivo="CARGA_INICIAL")
    assert sorted(d["clave"] for d in documentos) == sorted(f"{OBRA[n]}:carga:{carga_id}" for n in (1, 2, 3))
    por_obra = {d["obra_id"]: d for d in documentos}
    assert por_obra[OBRA[3]]["nivel"] == "SIN_DATOS"  # 2,08 % de plazo transcurrido
    for numero in (1, 2):
        documento = por_obra[OBRA[numero]]
        assert documento["nivel"] == nivel(documento["riesgo_retraso"])
        assert documento["variables"]["plazo_pct"] == {1: 33.33, 2: 50.0}[numero]
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA[2]))["nivel_riesgo"] == por_obra[OBRA[2]]["nivel"]


def test_programa_la_tarea_diaria_a_la_una_en_la_paz() -> None:
    programador = crear_programador("America/La_Paz")
    sin_servicio: Any = None
    programar_riesgo(programador, sin_servicio, "America/La_Paz")
    (trabajo,) = programador.get_jobs()
    assert trabajo.id == "riesgo_diario"
    assert isinstance(trabajo.trigger, CronTrigger)
    campos = {campo.name: str(campo) for campo in trabajo.trigger.fields}
    assert (campos["hour"], campos["minute"]) == ("1", "0")
    assert str(trabajo.trigger.timezone) == "America/La_Paz"
