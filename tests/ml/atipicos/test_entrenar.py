"""Entrenamiento del Isolation Forest (403, diseño «Entrenamiento», «Métricas mínimas» y «Pruebas»)."""

import csv
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.atipicos.dominio.variables import NOMBRES_VARIABLES
from ia.atipicos.infraestructura.modelo import ARCHIVO_ACTUAL, cargar_modelo
from ia.atipicos.infraestructura.repositorio_mongo import COLECCION
from ia.config import Configuracion
from ia.reportes.infraestructura.tablas import rpt_movimiento
from ml.atipicos.entrenar import EntrenamientoFallido, entrenar_con_reales, main, terminar
from ml.atipicos.generar import COLUMNAS


def escribir_csv_facil(ruta: Path, *, etiquetas_al_azar: bool = False) -> None:
    """20 obras con salidas normales parecidas y un 2 % de salidas con cantidad enorme (fáciles de aislar)."""
    rng = np.random.default_rng(1)
    filas: list[dict[str, Any]] = []
    for n in range(6000):
        anomalia = bool(rng.random() < 0.02)
        valores = {nombre: float(rng.normal(0, 0.3)) for nombre in NOMBRES_VARIABLES}
        if anomalia:
            valores["log_cantidad"] = 8.0 + float(rng.normal(0, 0.1))
            valores["z_obra_material"] = 10.0
            valores["razon_material"] = 9.0
        filas.append(
            {
                "movimiento_id": f"s{n}",
                "obra_id": f"obra-{n % 20:02d}",
                "material_id": "mat-01",
                "registrado_en": "2026-01-01T14:00:00Z",
                "periodo": "2026-01",
                "cantidad": 1.0,
                "anomalia": int(rng.random() < 0.02) if etiquetas_al_azar else int(anomalia),
                "tipo_anomalia": "",
                **valores,
            }
        )
    with ruta.open("w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS)
        escritor.writeheader()
        escritor.writerows(filas)


def test_sintetico_que_cumple_escribe_los_artefactos_y_cambia_actual(tmp_path: Path) -> None:
    datos = tmp_path / "salidas.csv"
    escribir_csv_facil(datos)
    modelos = tmp_path / "modelos"
    codigo = main(["--fuente", "sintetica", "--version", "ca-9.0.0", "--datos", str(datos), "--modelos", str(modelos)])
    assert codigo == 0
    carpeta = modelos / "ca-9.0.0"
    assert sorted(p.name for p in carpeta.iterdir()) == [
        "metricas.json",
        "modelo.joblib",
        "parametros.yaml",
        "percentiles.json",
    ]
    assert (modelos / ARCHIVO_ACTUAL).read_text(encoding="utf-8").strip() == "ca-9.0.0"
    metricas = json.loads((carpeta / "metricas.json").read_text(encoding="utf-8"))
    assert metricas["version"] == "ca-9.0.0"
    assert metricas["fuente"] == "sintetica"
    assert metricas["modelo"] == {
        "tipo": "IsolationForest",
        "n_estimators": 200,
        "contamination": 0.02,
        "max_samples": 4096,
        "random_state": 2026,
    }
    assert metricas["fallas"] == []
    assert metricas["sensibilidad"] >= 0.70
    assert metricas["precision"] >= 0.50
    assert metricas["auc_roc"] >= 0.85
    assert 0.01 <= metricas["proporcion_marcada"] <= 0.03
    # División por obra: 4 de las 20 obras quedan para la prueba.
    assert metricas["filas_prueba"] == 1200
    percentiles = json.loads((carpeta / "percentiles.json").read_text(encoding="utf-8"))
    assert set(percentiles) == set(NOMBRES_VARIABLES)
    assert "semilla" in (carpeta / "parametros.yaml").read_text(encoding="utf-8")
    # El servicio lo carga desde actual.txt.
    assert cargar_modelo(raiz=modelos).version == "ca-9.0.0"


def test_sin_las_metricas_minimas_falla_sin_tocar_actual(tmp_path: Path) -> None:
    datos = tmp_path / "salidas.csv"
    escribir_csv_facil(datos, etiquetas_al_azar=True)
    modelos = tmp_path / "modelos"
    modelos.mkdir()
    (modelos / ARCHIVO_ACTUAL).write_text("ca-1.0.0\n", encoding="utf-8")
    codigo = main(["--fuente", "sintetica", "--version", "ca-1.1.0", "--datos", str(datos), "--modelos", str(modelos)])
    assert codigo == 1
    assert (modelos / ARCHIVO_ACTUAL).read_text(encoding="utf-8") == "ca-1.0.0\n"
    assert not (modelos / "ca-1.1.0").exists()


def test_sin_datos_sinteticos_o_con_version_invalida_termina_con_error(tmp_path: Path) -> None:
    assert main(["--fuente", "sintetica", "--version", "ca-1.0.0", "--datos", str(tmp_path / "no.csv")]) == 1
    assert main(["--fuente", "sintetica", "--version", "1.0", "--modelos", str(tmp_path)]) == 2


async def insertar_salidas(motor: AsyncEngine, cantidad: int) -> None:
    rng = np.random.default_rng(3)
    inicio = datetime(2026, 1, 5, 14, 0, tzinfo=UTC)
    filas = []
    for n in range(cantidad):
        momento = inicio + timedelta(hours=8 * n)
        filas.append(
            {
                "movimiento_id": str(uuid.UUID(int=n + 1)),
                "obra_id": str(uuid.UUID(int=10_000 + n % 3)),
                "material_id": f"mat-{n % 4}",
                "partida_id": str(uuid.UUID(int=20_000)),
                "tipo": "SALIDA",
                "cantidad": round(float(rng.lognormal(3, 0.3)), 3),
                "precio_referencial": None,
                "periodo": momento.strftime("%Y-%m"),
                "supera_stock": False,
                "registrado_en": momento,
            }
        )
    filas.append({**filas[0], "movimiento_id": str(uuid.uuid4()), "tipo": "INGRESO", "partida_id": None})
    async with motor.begin() as conexion:
        await conexion.execute(insert(rpt_movimiento), filas)


def configuracion(url_db: str, url_mongo: str, base: str = "ia_db_pruebas") -> Configuracion:
    return Configuracion(reportes_db_url=url_db, mongo_url=url_mongo, mongo_db=base)


@pytest.mark.integracion
async def test_real_rechaza_con_menos_de_500_salidas(motor: AsyncEngine, url_migrada: str) -> None:
    await insertar_salidas(motor, 499)
    with pytest.raises(EntrenamientoFallido, match="499 salidas"):
        await entrenar_con_reales(configuracion(url_migrada, "mongodb://127.0.0.1:1"))


@pytest.mark.integracion
async def test_real_con_500_salidas_entrena_y_reporta_la_precision_observada(
    motor: AsyncEngine,
    url_migrada: str,
    url_mongo_pruebas: str,
    ia_db: AsyncDatabase[dict[str, Any]],
    tmp_path: Path,
) -> None:
    await insertar_salidas(motor, 520)
    await ia_db[COLECCION].insert_many(
        [{"_id": "a", "estado_revision": "CONFIRMADO"}, {"_id": "b", "estado_revision": "NORMAL"}]
        + [{"_id": f"p{n}", "estado_revision": "PENDIENTE"} for n in range(3)]
    )
    resultado = await entrenar_con_reales(configuracion(url_migrada, url_mongo_pruebas, ia_db.name))
    assert resultado.metricas["filas_entrenamiento"] == 520  # el ingreso no cuenta
    assert resultado.metricas["proporcion_marcada"] <= 0.03
    assert resultado.metricas["revisiones"] == {"NORMAL": 1, "CONFIRMADO": 1}
    assert resultado.metricas["precision_observada"] == 0.5
    assert resultado.parametros["fuente"] == "real"
    assert terminar(resultado, "ca-1.1.0", tmp_path, "real", 1.0) == 0
    assert (tmp_path / ARCHIVO_ACTUAL).read_text(encoding="utf-8").strip() == "ca-1.1.0"
    assert json.loads((tmp_path / "ca-1.1.0" / "metricas.json").read_text(encoding="utf-8"))["fuente"] == "real"
