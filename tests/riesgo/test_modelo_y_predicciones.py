from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from ia.comun.mongo import asegurar_indices
from ia.riesgo.aplicacion.prediccion import calcular_prediccion
from ia.riesgo.aplicacion.puertos import ObraParaRiesgo, Prediccion
from ia.riesgo.dominio.variables import EstadoObra, Factor, PlanillaAprobadaObra, calcular_variables
from ia.riesgo.infraestructura.modelo_xgboost import ModeloXgboost, cargar_modelo
from ia.riesgo.infraestructura.predicciones_mongo import AlmacenPrediccionesMongo

MODELOS = Path(__file__).resolve().parents[2] / "modelos"
HOY = date(2026, 10, 28)
AHORA = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)


def _estado(
    avance: float, plazo: float, planillas: tuple[PlanillaAprobadaObra, ...], observadas: int = 0
) -> EstadoObra:
    return EstadoObra(date(2026, 1, 1), 360, 2_000_000.0, avance, plazo, planillas, observadas, 0, 0)


ATRASADA = _estado(10.0, 80.0, (PlanillaAprobadaObra("2026-09", 2.0), PlanillaAprobadaObra("2026-08", 3.0)), 2)
AL_DIA = _estado(
    70.0,
    60.0,
    (
        PlanillaAprobadaObra("2026-08", 12.0),
        PlanillaAprobadaObra("2026-09", 10.0),
        PlanillaAprobadaObra("2026-10", 11.0),
    ),
)


@pytest.fixture(scope="module")
def modelo() -> ModeloXgboost:
    cargado = cargar_modelo(MODELOS)
    assert cargado is not None
    return cargado


def test_carga_la_version_de_actual_txt(modelo: ModeloXgboost) -> None:
    info = modelo.info
    assert info.version == (MODELOS / "riesgo_retraso" / "actual.txt").read_text(encoding="utf-8").strip()
    assert info.datos == "SINTETICOS"
    assert info.filas_entrenamiento == 9600
    assert set(info.metricas) == {"aucRoc", "aucRocRegla", "recallMedio", "precisionAlto", "brier"}
    assert info.umbrales == {"medio": 0.3, "alto": 0.6}
    assert info.variables[5] == ("relacion_ritmo", "Ritmo reciente / ritmo necesario")


def test_prediccion_de_filas_conocidas(modelo: ModeloXgboost) -> None:
    alta, contribuciones = modelo.predecir(calcular_variables(ATRASADA, HOY))
    baja, _ = modelo.predecir(calcular_variables(AL_DIA, HOY))
    assert alta == pytest.approx(0.9763, abs=1e-4)
    assert baja == pytest.approx(0.0628, abs=1e-4)
    assert set(contribuciones) == set(calcular_variables(ATRASADA, HOY))


def test_sin_modelo_devuelve_none(tmp_path: Path) -> None:
    assert cargar_modelo(tmp_path) is None
    assert cargar_modelo(MODELOS, "rr-0.0.0") is None


def test_calcular_prediccion_con_nivel_y_factores(modelo: ModeloXgboost) -> None:
    obra = ObraParaRiesgo("o1", "EN_EJECUCION", 80.0, ATRASADA)
    prediccion = calcular_prediccion(modelo, obra, HOY, "DIARIO", "o1:diario:2026-10-28", AHORA)
    assert prediccion.nivel == "ALTO"
    assert prediccion.riesgo_retraso == pytest.approx(0.9763, abs=1e-4)
    assert 1 <= len(prediccion.factores) <= 3
    assert prediccion.factores[0].peso >= prediccion.factores[-1].peso > 0.05
    assert prediccion.version_modelo == modelo.info.version
    assert set(prediccion.variables) == set(calcular_variables(ATRASADA, HOY))


@pytest.mark.parametrize(("estado", "plazo"), [("SIN_CONTRATO", 50.0), ("EN_EJECUCION", 4.9), ("EN_EJECUCION", None)])
def test_calcular_prediccion_sin_datos(modelo: ModeloXgboost, estado: str, plazo: float | None) -> None:
    prediccion = calcular_prediccion(modelo, ObraParaRiesgo("o1", estado, plazo, ATRASADA), HOY, "DIARIO", "k", AHORA)
    assert (prediccion.nivel, prediccion.riesgo_retraso, prediccion.factores) == ("SIN_DATOS", None, ())


def _prediccion(clave: str, obra: str, riesgo: float | None, minutos: int, nivel: str = "MEDIO") -> Prediccion:
    return Prediccion(
        clave=clave,
        obra_id=obra,
        riesgo_retraso=riesgo,
        nivel=nivel,
        factores=(Factor("brecha", "El avance está 20 puntos por debajo del plazo transcurrido", 0.4),),
        variables={"brecha": 20.0},
        version_modelo="rr-1.0.0",
        motivo="DIARIO",
        calculada_en=AHORA + timedelta(minutes=minutos),
    )


@pytest.mark.integracion
async def test_guarda_una_vez_por_clave_con_indice_unico(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    await asegurar_indices(ia_db)
    almacen = AlmacenPrediccionesMongo(ia_db)
    assert await almacen.guardar(_prediccion("o1:evento-1", "o1", 0.4, 0)) is True
    assert await almacen.guardar(_prediccion("o1:evento-1", "o1", 0.9, 5)) is False
    assert await ia_db["prediccion"].count_documents({}) == 1
    with pytest.raises(DuplicateKeyError):
        await ia_db["prediccion"].insert_one({"clave": "o1:evento-1"})
    documento = await ia_db["prediccion"].find_one({"clave": "o1:evento-1"})
    assert documento is not None
    assert documento["riesgo_retraso"] == 0.4
    assert documento["factores"] == [
        {"variable": "brecha", "descripcion": "El avance está 20 puntos por debajo del plazo transcurrido", "peso": 0.4}
    ]


@pytest.mark.integracion
async def test_ultimas_e_historial(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    almacen = AlmacenPrediccionesMongo(ia_db)
    for i, riesgo in enumerate((0.2, 0.5, 0.7)):
        await almacen.guardar(_prediccion(f"o1:{i}", "o1", riesgo, i))
    await almacen.guardar(_prediccion("o1:sin", "o1", None, 3, nivel="SIN_DATOS"))
    await almacen.guardar(_prediccion("o2:0", "o2", 0.1, 0))
    ultimas = await almacen.ultimas(["o1", "o2", "o3"])
    assert set(ultimas) == {"o1", "o2"}
    assert ultimas["o1"].nivel == "SIN_DATOS"
    assert ultimas["o1"].calculada_en == AHORA + timedelta(minutes=3)
    historial = await almacen.historial("o1", 2)
    assert [p.riesgo_retraso for p in historial] == [0.5, 0.7]
    assert [p.riesgo_retraso for p in await almacen.historial("o1", 30)] == [0.2, 0.5, 0.7]
