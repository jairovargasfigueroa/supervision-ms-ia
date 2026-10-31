"""Carga del modelo versionado y evaluación (403, diseño «Entrenamiento»)."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import joblib
import pytest

from apoyo.atipicos import modelo_sintetico_chico
from ia.atipicos.dominio.variables import Historial, SalidaMaterial, calcular_variables_salida
from ia.atipicos.infraestructura.modelo import (
    ModeloNoDisponible,
    cargar_modelo,
    cargar_modelo_o_nada,
    version_actual,
)


def guardar_version(raiz: Path, version: str) -> None:
    modelo = modelo_sintetico_chico(obras=4)
    carpeta = raiz / version
    carpeta.mkdir(parents=True)
    joblib.dump(modelo.modelo, carpeta / "modelo.joblib")
    (carpeta / "percentiles.json").write_text(json.dumps(modelo.percentiles.como_dict()), encoding="utf-8")


def test_carga_la_version_de_actual_o_la_pedida(tmp_path: Path) -> None:
    guardar_version(tmp_path, "ca-1.0.0")
    guardar_version(tmp_path, "ca-1.1.0")
    (tmp_path / "actual.txt").write_text("ca-1.0.0\n", encoding="utf-8")
    assert version_actual(tmp_path) == "ca-1.0.0"
    assert cargar_modelo(raiz=tmp_path).version == "ca-1.0.0"
    assert cargar_modelo("ca-1.1.0", raiz=tmp_path).version == "ca-1.1.0"


def test_sin_modelo_registra_el_error_y_devuelve_nada(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR):
        assert cargar_modelo_o_nada(raiz=tmp_path) is None
    assert "sin modelo" in caplog.text
    with pytest.raises(ModeloNoDisponible, match="inválida"):
        cargar_modelo("../otro", raiz=tmp_path)
    with pytest.raises(ModeloNoDisponible, match=r"ca-2.0.0"):
        cargar_modelo("ca-2.0.0", raiz=tmp_path)


def test_evalua_con_puntaje_entre_0_y_1_y_motivos_solo_si_es_atipica() -> None:
    modelo = modelo_sintetico_chico(obras=4)
    normal = calcular_variables_salida(
        SalidaMaterial("s", "o", "m", 20, "2026-07", False, datetime(2026, 7, 15, 14, 0, tzinfo=UTC)), Historial()
    )
    rara = calcular_variables_salida(
        SalidaMaterial("s", "o", "m", 90_000, "2026-07", True, datetime(2026, 7, 19, 6, 40, tzinfo=UTC)), Historial()
    )
    for variables in (normal, rara):
        evaluacion = modelo.evaluar(variables)
        assert 0 < evaluacion.puntaje <= 1
        assert evaluacion.version == "ca-0.9.0"
        assert (1 <= len(evaluacion.motivos) <= 3) if evaluacion.atipica else evaluacion.motivos == []
    assert modelo.evaluar(rara).atipica
    assert modelo.evaluar(rara).puntaje > modelo.evaluar(normal).puntaje
