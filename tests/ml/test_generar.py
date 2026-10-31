import hashlib
from pathlib import Path
from typing import Any

import pytest

from ia.riesgo.dominio import variables as dominio
from ml.riesgo import generar


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def test_misma_semilla_mismo_csv_y_otra_semilla_otro() -> None:
    parametros = generar.leer_parametros()
    a, _ = generar.generar(parametros, 200, 2026)
    b, _ = generar.generar(parametros, 200, 2026)
    c, _ = generar.generar(parametros, 200, 7)
    assert _hash(generar.a_csv(a)) == _hash(generar.a_csv(b))
    assert _hash(generar.a_csv(a)) != _hash(generar.a_csv(c))


def test_comando_genera_4000_obras_con_proporcion_en_el_objetivo(tmp_path: Path) -> None:
    salida = tmp_path / "riesgo.csv"
    assert generar.main(["--obras", "4000", "--semilla", "2026", "--salida", str(salida)]) == 0
    lineas = salida.read_text(encoding="utf-8").splitlines()
    assert lineas[0].split(",") == list(generar.COLUMNAS)
    assert len(lineas) == 1 + 12000
    _, resumen = generar.generar(generar.leer_parametros(), 4000, 2026)
    assert 0.25 <= resumen["proporcion_retrasadas"] <= 0.45


def test_las_variables_salen_de_calcular_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas: list[Any] = []
    original = dominio.calcular_variables

    def espia(estado: dominio.EstadoObra, hoy: Any) -> dict[str, float]:
        llamadas.append((estado, hoy))
        return original(estado, hoy)

    monkeypatch.setattr(generar, "calcular_variables", espia)
    filas, _ = generar.generar(generar.leer_parametros(), 50, 2026)
    assert len(llamadas) == len(filas) == 150
    estado, hoy = llamadas[0]
    assert filas[0]["fecha_corte"] == hoy.isoformat()
    assert filas[0]["brecha"] == round(original(estado, hoy)["brecha"], 4)


def test_cortes_entre_5_y_95_por_ciento_del_plazo() -> None:
    filas, _ = generar.generar(generar.leer_parametros(), 300, 2026)
    assert all(4.9 <= fila["plazo_pct"] <= 95.1 for fila in filas)
    assert {fila["se_retrasa"] for fila in filas} == {0, 1}
