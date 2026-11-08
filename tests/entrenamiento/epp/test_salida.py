"""Archivos versionados del modelo entregado a la app de obra (301): ``epp.json`` y ``metricas.json``."""

import json
from pathlib import Path
from typing import Any

SALIDA = Path(__file__).resolve().parents[3] / "entrenamiento" / "epp" / "salida"
ATRIBUTOS = ["casco", "chaleco"]


def _leer(nombre: str) -> Any:
    return json.loads((SALIDA / nombre).read_text(encoding="utf-8"))


def test_epp_json_tiene_version_umbrales_entrada_y_orden_de_salidas() -> None:
    configuracion = _leer("epp.json")

    assert configuracion["versionModelo"] == f"epp-{configuracion['version']}"
    assert 1 <= len(configuracion["versionModelo"]) <= 40  # Obras acepta de 1 a 40 caracteres
    assert configuracion["salidas"] == ATRIBUTOS
    assert configuracion["entrada"] == {"alto": 224, "ancho": 224, "canales": 3, "tipo": "float32", "rango": [0, 255]}
    assert all(0 < configuracion["umbrales"][a] < 1 for a in ATRIBUTOS)


def test_metricas_del_modelo_entregado_cumplen_los_minimos_del_diseno() -> None:
    metricas = _leer("metricas.json")
    configuracion = _leer("epp.json")

    assert metricas["versionModelo"] == configuracion["versionModelo"]
    assert metricas["cumpleMinimos"], metricas["faltas"]
    for a in ATRIBUTOS:
        prueba = metricas["prueba"][a]
        assert prueba["umbral"] == configuracion["umbrales"][a]
        assert prueba["sensibilidad_falta"] >= 0.85
        assert prueba["precision_falta"] >= 0.75
        assert prueba["exactitud"] >= 0.85
    assert sum(metricas["tamanosMb"].values()) <= 10
    assert metricas["diferenciaMaximaKerasTflite"] <= 0.02


def test_el_tflite_versionado_es_el_medido() -> None:
    tamano = (SALIDA / "clasificador_epp.tflite").stat().st_size / (1024 * 1024)

    assert round(tamano, 3) == _leer("metricas.json")["tamanosMb"]["clasificador_epp.tflite"]
