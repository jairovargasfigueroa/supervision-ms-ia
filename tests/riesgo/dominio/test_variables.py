import math
from datetime import date

import pytest

from ia.riesgo.dominio.variables import (
    VARIABLES,
    EstadoObra,
    PlanillaAprobadaObra,
    calcular_variables,
    elegir_factores,
    nivel,
    sin_datos,
    texto_factor,
)

HOY = date(2026, 10, 28)


def _estado(**cambios: object) -> EstadoObra:
    base: dict[str, object] = {
        "fecha_inicio": date(2026, 6, 30),
        "plazo_dias": 360,
        "monto_contrato": 2773800.0,
        "avance_pct": 0.0,
        "plazo_pct": 33.33,
        "planillas_aprobadas": (),
        "planillas_observadas": 0,
        "atipicos_90d": 0,
        "supera_stock_90d": 0,
    }
    base.update(cambios)
    return EstadoObra(**base)  # type: ignore[arg-type]


def test_sin_planillas() -> None:
    v = calcular_variables(_estado(), HOY)
    assert list(v) == list(VARIABLES)
    assert v["brecha"] == pytest.approx(33.33)
    assert v["ritmo_reciente"] == 0
    # meses restantes = 360 × (1 − 0,3333) / 30,44 = 7,885; requerido = 100 / 7,885
    assert v["ritmo_requerido"] == pytest.approx(100 / (360 * (1 - 0.3333) / 30.44))
    assert v["relacion_ritmo"] == 0
    assert v["meses_sin_planilla"] == 4  # desde junio (inicio) hasta octubre
    assert v["log_monto"] == pytest.approx(math.log(2773800.0))
    assert v["plazo_dias"] == 360


def test_con_una_planilla() -> None:
    estado = _estado(
        avance_pct=12.6, planillas_aprobadas=(PlanillaAprobadaObra("2026-07", 12.6),), planillas_observadas=1
    )
    v = calcular_variables(estado, HOY)
    assert v["ritmo_reciente"] == pytest.approx(12.6)
    assert v["meses_sin_planilla"] == 3
    assert v["planillas_observadas"] == 1
    assert v["relacion_ritmo"] == pytest.approx(12.6 / v["ritmo_requerido"])


def test_con_cuatro_planillas_usa_las_tres_ultimas_por_periodo() -> None:
    planillas = (
        PlanillaAprobadaObra("2026-09", 6.0),
        PlanillaAprobadaObra("2026-06", 30.0),
        PlanillaAprobadaObra("2026-08", 9.0),
        PlanillaAprobadaObra("2026-07", 12.0),
    )
    v = calcular_variables(_estado(avance_pct=57.0, planillas_aprobadas=planillas), HOY)
    assert v["ritmo_reciente"] == pytest.approx(9.0)  # (12 + 9 + 6) / 3
    assert v["meses_sin_planilla"] == 1


def test_plazo_vencido_usa_medio_mes_como_minimo() -> None:
    v = calcular_variables(_estado(plazo_pct=100.0, avance_pct=80.0), HOY)
    assert v["ritmo_requerido"] == pytest.approx(20 / 0.5)


def test_ritmo_requerido_cero_da_relacion_tres() -> None:
    estado = _estado(avance_pct=100.0, planillas_aprobadas=(PlanillaAprobadaObra("2026-10", 5.0),))
    v = calcular_variables(estado, HOY)
    assert v["ritmo_requerido"] == 0
    assert v["relacion_ritmo"] == 3
    rapido = _estado(avance_pct=40.0, planillas_aprobadas=(PlanillaAprobadaObra("2026-10", 90.0),))
    assert calcular_variables(rapido, HOY)["relacion_ritmo"] == 3  # recortada


def test_niveles_en_los_bordes() -> None:
    assert nivel(0.0) == "BAJO"
    assert nivel(0.2999) == "BAJO"
    assert nivel(0.30) == "MEDIO"
    assert nivel(0.5999) == "MEDIO"
    assert nivel(0.60) == "ALTO"
    assert nivel(1.0) == "ALTO"


def test_sin_datos() -> None:
    assert sin_datos("SIN_CONTRATO", 50.0)
    assert sin_datos("EN_EJECUCION", 4.99)
    assert sin_datos("EN_EJECUCION", None)
    assert not sin_datos("EN_EJECUCION", 5.0)


def test_factores_hasta_tres_positivos_mayores_a_005() -> None:
    v = calcular_variables(_estado(avance_pct=10.0, planillas_observadas=2), HOY)
    contribuciones = {
        "relacion_ritmo": 0.81,
        "brecha": 0.4,
        "planillas_observadas": 0.2,
        "plazo_pct": 0.1,
        "avance_pct": 0.05,
        "log_monto": -0.3,
        "bias": 2.0,
    }
    factores = elegir_factores(v, contribuciones)
    assert [f.variable for f in factores] == ["relacion_ritmo", "brecha", "planillas_observadas"]
    assert factores[0].peso == 0.81
    assert factores[0].descripcion == (
        "El ritmo de avance de los últimos meses es menor al necesario para terminar a tiempo"
    )
    assert factores[1].descripcion == "El avance está 23,3 puntos por debajo del plazo transcurrido"
    assert factores[2].descripcion == "Se observaron 2 planillas"
    assert elegir_factores(v, {"brecha": 0.05, "log_monto": -1.0}) == []


def test_textos_de_factores() -> None:
    assert texto_factor("plazo_pct", 50.0) == "Ya transcurrió 50 % del plazo"
    assert texto_factor("avance_pct", 16.34) == "El avance es de solo 16,3 %"
    assert texto_factor("meses_sin_planilla", 1) == "Hace 1 mes que no se aprueba una planilla"
    assert texto_factor("meses_sin_planilla", 3) == "Hace 3 meses que no se aprueba una planilla"
    assert texto_factor("atipicos_90d", 4) == "Hubo 4 consumos de material atípicos en los últimos 90 días"
    assert texto_factor("supera_stock_90d", 1) == "Hubo 1 salida de material sin stock suficiente"
    assert texto_factor("plazo_dias", 720) == "El plazo de la obra es largo (720 días)"
    assert texto_factor("ritmo_requerido", 12.68) == "Para terminar a tiempo se necesitan 12,7 puntos por mes"
    for variable in VARIABLES:
        assert texto_factor(variable, 2.0)


def test_un_conteo_en_cero_no_es_factor() -> None:
    v = calcular_variables(_estado(planillas_aprobadas=(PlanillaAprobadaObra("2026-10", 5.0),)), HOY)
    assert v["meses_sin_planilla"] == 0
    factores = elegir_factores(v, {"meses_sin_planilla": 1.6, "planillas_observadas": 0.9, "plazo_pct": 0.8})
    assert [f.variable for f in factores] == ["plazo_pct"]
