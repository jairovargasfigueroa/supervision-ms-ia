"""Equivalencia Keras frente a TFLite float16 (301).

Necesitan TensorFlow: en el entorno del servicio (``uv run pytest``) se saltan; corren en el de entrenamiento::

    entrenamiento/epp/.venv/Scripts/python -m pytest -p no:cacheprovider --noconftest tests/entrenamiento -q
"""

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("tensorflow")

from entrenamiento.epp import clasificador

SALIDA = clasificador.CARPETA_SALIDA


def _imagenes(n: int, semilla: int = 2026) -> np.ndarray:
    return np.random.default_rng(semilla).integers(0, 256, size=(n, clasificador.LADO, clasificador.LADO, 3))


@pytest.fixture(scope="module")
def modelo_sin_entrenar() -> object:
    import keras

    keras.utils.set_random_seed(2026)
    return clasificador.crear_modelo(pesos_base=None)


def test_la_exportacion_float16_da_las_mismas_predicciones(modelo_sin_entrenar: object, tmp_path: Path) -> None:
    ruta = clasificador.exportar_tflite(modelo_sin_entrenar, tmp_path)

    diferencia = clasificador.comprobar_equivalencia(modelo_sin_entrenar, ruta, _imagenes(50))

    assert diferencia <= clasificador.DIFERENCIA_MAXIMA_TFLITE
    salida = clasificador.predecir_tflite(ruta, _imagenes(2))
    assert salida.shape == (2, 2)
    assert ((salida >= 0) & (salida <= 1)).all()


def test_la_equivalencia_falla_con_un_mensaje_claro(modelo_sin_entrenar: object, tmp_path: Path) -> None:
    import keras

    ruta = clasificador.exportar_tflite(modelo_sin_entrenar, tmp_path)
    otro = keras.models.clone_model(modelo_sin_entrenar)  # mismos tamaños, otros pesos
    otro.set_weights([w + 0.5 for w in otro.get_weights()])

    with pytest.raises(clasificador.EquivalenciaTfliteError, match="difiere del modelo de Keras"):
        clasificador.comprobar_equivalencia(otro, ruta, _imagenes(50))


def test_el_modelo_entregado_coincide_con_keras_en_50_recortes_de_prueba() -> None:
    keras_guardado = SALIDA / "clasificador_epp.keras"
    recortes = clasificador.CARPETA_RECORTES / "recortes.csv"
    if not keras_guardado.exists() or not recortes.exists():
        pytest.skip("Falta el modelo de Keras o los recortes (no se versionan): corre antes el notebook.")
    import keras

    modelo = keras.models.load_model(keras_guardado)
    prueba = clasificador.cargar_particion(clasificador.leer_recortes(), "prueba")

    muestra = prueba.imagenes(range(clasificador.RECORTES_EQUIVALENCIA))
    diferencia = clasificador.comprobar_equivalencia(modelo, SALIDA / "clasificador_epp.tflite", muestra)

    assert diferencia <= clasificador.DIFERENCIA_MAXIMA_TFLITE
