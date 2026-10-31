"""Generador sintético de la 403: reproducible con la semilla, 2 % de anomalías de los 4 tipos (diseño, «Pruebas»)."""

import csv
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from ia.atipicos.dominio.variables import NOMBRES_VARIABLES
from ml.atipicos.generar import (
    COLUMNAS,
    PARAMETROS,
    RAFAGA,
    TIPOS_ANOMALIA,
    SalidaSintetica,
    calcular_filas,
    generar_salidas,
    leer_parametros,
    main,
)

LA_PAZ = ZoneInfo("America/La_Paz")


def chicos(**cambios: Any) -> dict[str, Any]:
    parametros = leer_parametros()
    parametros.update({"obras": 6, **cambios})
    return parametros


@pytest.fixture(scope="module")
def completas() -> list[SalidaSintetica]:
    """Con los parámetros del equipo y la semilla 2026 (≈ 50 000 salidas)."""
    parametros = leer_parametros()
    return generar_salidas(parametros, int(parametros["semilla"]))


def test_misma_semilla_mismas_filas_y_otra_semilla_otras() -> None:
    primera = calcular_filas(generar_salidas(chicos(), 2026))
    segunda = calcular_filas(generar_salidas(chicos(), 2026))
    otra = calcular_filas(generar_salidas(chicos(), 7))
    assert primera == segunda
    assert primera != otra
    assert list(primera[0]) == list(COLUMNAS)


def test_los_parametros_del_equipo_son_los_del_diseno() -> None:
    parametros = leer_parametros()
    assert parametros["semilla"] == 2026
    assert (parametros["obras"], parametros["materiales"], parametros["meses_por_obra"]) == (60, 25, 12)
    assert parametros["mediana_material"] == [2.0, 200.0]
    assert parametros["factor_obra"] == [0.5, 2.0]
    assert parametros["salidas_por_semana"] == [3, 15]
    assert parametros["sigma_cantidad"] == 0.35
    assert (parametros["hora_media"], parametros["hora_desvio"]) == (10.0, 2.5)
    assert parametros["prob_supera_stock"] == 0.01
    assert parametros["anomalias"]["proporcion"] == 0.02


def test_cerca_de_50000_salidas_con_2_por_ciento_de_anomalias_de_4_tipos(completas: list[SalidaSintetica]) -> None:
    assert 45_000 <= len(completas) <= 55_000
    tipos = Counter(s.tipo_anomalia for s in completas if s.tipo_anomalia)
    anomalas = sum(tipos.values())
    assert anomalas / len(completas) == pytest.approx(0.02, abs=0.001)
    assert set(tipos) == set(TIPOS_ANOMALIA)
    for tipo in TIPOS_ANOMALIA:
        assert tipos[tipo] / anomalas == pytest.approx(0.25, abs=0.02), tipo


def test_salidas_normales_lunes_a_sabado_de_7_a_18(completas: list[SalidaSintetica]) -> None:
    normales = [s.salida for s in completas if s.tipo_anomalia is None]
    locales = [s.registrado_en.astimezone(LA_PAZ) for s in normales]
    assert all(local.weekday() < 6 for local in locales)
    assert all(7 <= local.hour + local.minute / 60 + local.second / 3600 <= 18 for local in locales)
    assert all(s.periodo == local.strftime("%Y-%m") for s, local in zip(normales, locales, strict=True))
    con_stock = sum(s.supera_stock for s in normales) / len(normales)
    assert con_stock == pytest.approx(0.01, abs=0.003)
    assert len({s.obra_id for s in normales}) == 60
    assert len({s.material_id for s in normales}) <= 25


def test_cada_tipo_de_anomalia_es_como_dice_el_diseno(completas: list[SalidaSintetica]) -> None:
    por_tipo: dict[str, list[SalidaSintetica]] = {tipo: [] for tipo in TIPOS_ANOMALIA}
    for s in completas:
        if s.tipo_anomalia:
            por_tipo[s.tipo_anomalia].append(s)
    assert all(s.salida.supera_stock for s in por_tipo["STOCK"])
    for s in por_tipo["HORARIO"]:
        local = s.salida.registrado_en.astimezone(LA_PAZ)
        assert local.weekday() == 6 or local.hour >= 21 or local.hour < 5
    # Ráfagas: de 3 a 6 salidas del mismo material en la obra dentro de una hora.
    rafagas = sorted((s.salida for s in por_tipo[RAFAGA]), key=lambda s: (s.obra_id, s.material_id, s.registrado_en))
    grupos: list[list[Any]] = []
    for salida in rafagas:
        ultimo = grupos[-1][-1] if grupos else None
        if (
            ultimo is not None
            and (ultimo.obra_id, ultimo.material_id) == (salida.obra_id, salida.material_id)
            and salida.registrado_en - grupos[-1][0].registrado_en <= timedelta(hours=1)
        ):
            grupos[-1].append(salida)
        else:
            grupos.append([salida])
    assert all(3 <= len(grupo) <= 6 for grupo in grupos)


def test_main_escribe_el_csv_con_la_marca_y_las_variables(tmp_path: Path) -> None:
    parametros = tmp_path / "parametros.yaml"
    parametros.write_text(
        PARAMETROS.read_text(encoding="utf-8").replace("obras: 60", "obras: 4"),
        encoding="utf-8",
    )
    destino = tmp_path / "datos" / "salidas.csv"
    assert main(["--semilla", "2026", "--parametros", str(parametros), "--salida", str(destino)]) == 0
    with destino.open(encoding="utf-8") as archivo:
        filas = list(csv.DictReader(archivo))
    assert filas
    assert set(NOMBRES_VARIABLES) <= set(filas[0])
    assert {fila["anomalia"] for fila in filas} == {"0", "1"}
    assert [fila["registrado_en"] for fila in filas] == sorted(fila["registrado_en"] for fila in filas)
