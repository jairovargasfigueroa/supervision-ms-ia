import json
import uuid
from pathlib import Path

import pandas as pd
import pytest
from ml.riesgo import entrenar, generar

RELAJADOS = {"aucRoc": 0.5, "mejoraSobreRegla": -1.0, "recallMedio": 0.0, "precisionAlto": 0.0, "brierMaximo": 1.0}
IMPOSIBLES = {"aucRoc": 1.01, "mejoraSobreRegla": 0.05, "recallMedio": 0.8, "precisionAlto": 0.7, "brierMaximo": 0.18}


@pytest.fixture(scope="module")
def csv_sintetico(tmp_path_factory: pytest.TempPathFactory) -> Path:
    ruta = tmp_path_factory.mktemp("datos") / "riesgo_sintetico.csv"
    assert generar.main(["--obras", "300", "--semilla", "2026", "--salida", str(ruta)]) == 0
    return ruta


def _rejilla_chica(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entrenar, "REJILLA", {"max_depth": [3], "learning_rate": [0.1]})


def test_sintetico_escribe_los_cuatro_artefactos_y_actual(
    csv_sintetico: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rejilla_chica(monkeypatch)
    codigo = entrenar.ejecutar("sintetica", "rr-9.0.0", csv_sintetico, salida=tmp_path, minimos=RELAJADOS)
    assert codigo == 0
    carpeta = tmp_path / "rr-9.0.0"
    assert sorted(p.name for p in carpeta.iterdir()) == [
        "metricas.json",
        "modelo.ubj",
        "parametros.yaml",
        "variables.json",
    ]
    assert (tmp_path / "actual.txt").read_text(encoding="utf-8").strip() == "rr-9.0.0"
    metricas = json.loads((carpeta / "metricas.json").read_text(encoding="utf-8"))
    assert metricas["datos"] == "SINTETICOS"
    assert set(metricas["metricas"]) == {"aucRoc", "aucRocRegla", "recallMedio", "precisionAlto", "brier"}
    assert metricas["filasEntrenamiento"] + metricas["filasPrueba"] == 900
    assert metricas["umbrales"] == {"medio": 0.3, "alto": 0.6}
    assert metricas["hiperparametros"]["tree_method"] == "hist"
    variables = json.loads((carpeta / "variables.json").read_text(encoding="utf-8"))["variables"]
    assert variables[0]["nombre"] == "plazo_pct"
    assert len(variables) == 12
    # La misma versión no se sobrescribe.
    assert entrenar.ejecutar("sintetica", "rr-9.0.0", csv_sintetico, salida=tmp_path, minimos=RELAJADOS) == 1


def test_sin_las_metricas_minimas_no_publica_ni_toca_actual(
    csv_sintetico: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rejilla_chica(monkeypatch)
    (tmp_path / "actual.txt").write_text("rr-1.0.0\n", encoding="utf-8")
    codigo = entrenar.ejecutar("sintetica", "rr-9.0.1", csv_sintetico, salida=tmp_path, minimos=IMPOSIBLES)
    assert codigo == 1
    assert not (tmp_path / "rr-9.0.1").exists()
    assert (tmp_path / "actual.txt").read_text(encoding="utf-8").strip() == "rr-1.0.0"


def _csv_real(origen: Path, destino: Path, obras: int) -> Path:
    datos = pd.read_csv(origen)
    ids = list(dict.fromkeys(datos["obra_id"]))[:obras]
    reales = datos[datos["obra_id"].isin(ids)].copy()
    reales["obra_id"] = reales["obra_id"].map({i: str(uuid.uuid5(uuid.NAMESPACE_URL, i)) for i in ids})
    reales.to_csv(destino, index=False)
    return destino


def test_real_escribe_datos_reales_sin_parametros(
    csv_sintetico: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _rejilla_chica(monkeypatch)
    archivo = _csv_real(csv_sintetico, tmp_path / "reales.csv", 120)
    salida = tmp_path / "modelos"
    assert entrenar.ejecutar("real", "rr-1.1.0", archivo, salida=salida, minimos=RELAJADOS) == 0
    metricas = json.loads((salida / "rr-1.1.0" / "metricas.json").read_text(encoding="utf-8"))
    assert metricas["datos"] == "REALES"
    assert metricas["obras"] == 120
    assert not (salida / "rr-1.1.0" / "parametros.yaml").exists()


def test_real_rechaza_columnas_que_faltan_y_menos_de_50_obras(csv_sintetico: Path, tmp_path: Path) -> None:
    sin_columna = tmp_path / "sin_columna.csv"
    pd.read_csv(csv_sintetico).drop(columns=["relacion_ritmo"]).to_csv(sin_columna, index=False)
    with pytest.raises(entrenar.DatosInvalidos, match="relacion_ritmo"):
        entrenar.leer_datos(sin_columna, "REALES")
    pocas = _csv_real(csv_sintetico, tmp_path / "pocas.csv", 49)
    with pytest.raises(entrenar.DatosInvalidos, match="al menos 50"):
        entrenar.leer_datos(pocas, "REALES")
    assert entrenar.ejecutar("real", "rr-1.1.0", pocas, salida=tmp_path / "m") == 1
    assert entrenar.ejecutar("real", "rr-1.1.0", None, salida=tmp_path / "m") == 1
    assert entrenar.ejecutar("sintetica", "1.0", csv_sintetico, salida=tmp_path / "m") == 1


def test_modelo_versionado_cumple_las_metricas_minimas() -> None:
    raiz = Path(entrenar.MODELOS)
    version = (raiz / "actual.txt").read_text(encoding="utf-8").strip()
    metricas = json.loads((raiz / version / "metricas.json").read_text(encoding="utf-8"))
    m = metricas["metricas"]
    assert m["aucRoc"] >= 0.80
    assert m["aucRoc"] - m["aucRocRegla"] >= 0.05
    assert m["recallMedio"] >= 0.80
    assert m["precisionAlto"] >= 0.70
    assert m["brier"] <= 0.18
    assert (raiz / version / "modelo.ubj").stat().st_size < 1_000_000
