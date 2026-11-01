"""Clasificador de casco y chaleco por persona (301): datos, modelo, entrenamiento, métricas, umbrales y exportación.

Lo llama ``entrenar_clasificador_epp.ipynb``; también se puede correr completo desde la terminal
(desde ``services/ia``)::

    entrenamiento/epp/.venv/Scripts/python -m entrenamiento.epp.clasificador

Modelo del diseño: ``MobileNetV3Small(include_top=False, weights="imagenet", include_preprocessing=True)`` +
``GlobalAveragePooling`` + ``Dropout(0.2)`` + dos salidas ``Dense(1, sigmoid)`` (``casco`` y ``chaleco``).
Entrada: recorte de la persona de 224 × 224, RGB ``float32`` de 0 a 255 (la normalización está dentro del modelo).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

CARPETA_EPP = Path(__file__).resolve().parent
CARPETA_RECORTES = CARPETA_EPP / "datos" / "recortes"
CARPETA_SALIDA = CARPETA_EPP / "salida"

VERSION = "1.0.0"
LADO = 224
ATRIBUTOS = ("casco", "chaleco")
SEMILLA = 2026
LOTE = 32
DIFERENCIA_MAXIMA_TFLITE = 0.02
RECORTES_EQUIVALENCIA = 50

DATASETS = {
    "gsnvb": "Construction Safety (Roboflow 100, construction-safety-gsnvb), CC BY 4.0",
    "shel5k": "SHEL5K (Mendeley Data, doi:10.17632/9rcv8mm682.4), CC BY 4.0",
}

# Métricas mínimas del diseño («Métricas mínimas»), sobre el 15 % de prueba, con la falta de EPP como clase positiva.
MINIMOS = {
    "sensibilidad_falta": 0.85,
    "precision_falta": 0.75,
    "exactitud": 0.85,
    "tamano_tflite_mb": 10.0,  # los dos .tflite juntos (clasificador + detector de personas)
}


class MetricasInsuficientesError(RuntimeError):
    """El modelo no alcanza las métricas mínimas del diseño: no se incluye en la app."""


class EquivalenciaTfliteError(RuntimeError):
    """El ``.tflite`` no da las mismas predicciones que el modelo de Keras."""


def configurar_hilos(hilos: int | None = None) -> None:
    """Limita los hilos de TensorFlow (la máquina se comparte); ``EPP_HILOS`` o 4 por defecto."""
    import tensorflow as tf

    n = hilos or int(os.environ.get("EPP_HILOS", "4"))
    tf.config.threading.set_intra_op_parallelism_threads(n)
    tf.config.threading.set_inter_op_parallelism_threads(2)


# --- Datos ---------------------------------------------------------------------------------------------------------


@dataclass
class Particion:
    """Recortes de una división. Las imágenes se leen de disco al entrenar o predecir (no caben todas en memoria)."""

    archivos: list[str]  # rutas completas de los recortes JPEG de 224 × 224
    fuentes: np.ndarray  # (N,) nombre del dataset de cada recorte
    etiquetas: dict[str, np.ndarray]  # atributo → (N,) float32, 1 = lleva el EPP
    pesos: dict[str, np.ndarray]  # atributo → (N,) float32, 0 = no se pudo decidir

    def __len__(self) -> int:
        return len(self.archivos)

    def imagenes(self, indices: Sequence[int] | None = None) -> np.ndarray:
        """Recortes como ``uint8`` ``(n, 224, 224, 3)`` (para mostrarlos o comparar Keras con TFLite)."""
        from PIL import Image

        elegidos = range(len(self)) if indices is None else indices
        resultado = np.zeros((len(elegidos), LADO, LADO, 3), dtype=np.uint8)
        for i, j in enumerate(elegidos):
            with Image.open(self.archivos[j]) as img:
                resultado[i] = np.asarray(img.convert("RGB").resize((LADO, LADO)), dtype=np.uint8)
        return resultado

    def de_fuente(self, fuente: str) -> Particion:
        indices = np.flatnonzero(self.fuentes == fuente)
        return Particion(
            archivos=[self.archivos[i] for i in indices],
            fuentes=self.fuentes[indices],
            etiquetas={a: v[indices] for a, v in self.etiquetas.items()},
            pesos={a: v[indices] for a, v in self.pesos.items()},
        )


def leer_recortes(carpeta: Path = CARPETA_RECORTES) -> pd.DataFrame:
    ruta = carpeta / "recortes.csv"
    if not ruta.exists():
        raise FileNotFoundError(f"No existe {ruta}: corre antes preparar_recortes.")
    return pd.read_csv(ruta)


def cargar_particion(tabla: pd.DataFrame, division: str, carpeta: Path = CARPETA_RECORTES) -> Particion:
    filas = tabla[tabla["division"] == division].reset_index(drop=True)
    return Particion(
        archivos=[str(carpeta / archivo) for archivo in filas["archivo"]],
        fuentes=filas["fuente"].to_numpy(dtype=str) if "fuente" in filas else np.full(len(filas), "gsnvb"),
        etiquetas={a: filas[a].to_numpy(dtype=np.float32) for a in ATRIBUTOS},
        pesos={a: filas[f"peso_{a}"].to_numpy(dtype=np.float32) for a in ATRIBUTOS},
    )


def pesos_balanceados(particion: Particion) -> dict[str, np.ndarray]:
    """Peso de entrenamiento por recorte y atributo: 0 si no se pudo decidir; si no, ``n / (2 · n_clase)`` para que
    la clase minoritaria (por ejemplo «sin casco») pese lo mismo que la mayoritaria (decisión técnica)."""
    resultado: dict[str, np.ndarray] = {}
    for a in ATRIBUTOS:
        decididos = particion.pesos[a] > 0
        y = particion.etiquetas[a]
        n = float(decididos.sum())
        pesos = np.zeros_like(y)
        for clase in (0.0, 1.0):
            n_clase = float((decididos & (y == clase)).sum())
            if n_clase > 0:
                pesos[decididos & (y == clase)] = n / (2.0 * n_clase)
        resultado[a] = pesos.astype(np.float32)
    return resultado


def crear_aumentos() -> Any:
    """Aumentos del diseño: volteo horizontal, brillo y contraste, rotación ±10° y recorte aleatorio (90 % del lado,
    vuelto a 224). Solo en entrenamiento; no forman parte del modelo exportado."""
    import keras

    return keras.Sequential(
        [
            keras.layers.RandomFlip("horizontal"),
            keras.layers.RandomBrightness(0.2, value_range=(0.0, 255.0)),
            keras.layers.RandomContrast(0.2, value_range=(0.0, 255.0)),
            keras.layers.RandomRotation(10.0 / 360.0, fill_mode="reflect"),
            keras.layers.RandomCrop(int(LADO * 0.9), int(LADO * 0.9)),
            keras.layers.Resizing(LADO, LADO),
        ],
        name="aumentos",
    )


def _leer_jpeg(ruta: Any) -> Any:
    import tensorflow as tf

    imagen = tf.io.decode_jpeg(tf.io.read_file(ruta), channels=3)
    return tf.cast(tf.image.resize(imagen, (LADO, LADO)), tf.float32)


def crear_dataset(
    particion: Particion, pesos: Mapping[str, np.ndarray] | None = None, *, entrenamiento: bool = False
) -> Any:
    """``tf.data`` que lee los recortes de disco: ``(imagen float32 0–255, etiquetas, pesos)`` en tuplas en el orden de
    las salidas (Keras 3 no resuelve pesos por nombre de salida); sin ``pesos``, solo las imágenes (para predecir)."""
    import tensorflow as tf

    rutas = tf.data.Dataset.from_tensor_slices(particion.archivos)
    if pesos is None:
        return rutas.map(_leer_jpeg, num_parallel_calls=tf.data.AUTOTUNE).batch(LOTE).prefetch(tf.data.AUTOTUNE)
    etiquetas = tf.data.Dataset.from_tensor_slices(
        (
            tuple(particion.etiquetas[a][:, None] for a in ATRIBUTOS),
            tuple(np.asarray(pesos[a], dtype=np.float32) for a in ATRIBUTOS),
        )
    )
    datos = tf.data.Dataset.zip((rutas, etiquetas))
    if entrenamiento:
        datos = datos.shuffle(len(particion), seed=SEMILLA, reshuffle_each_iteration=True)
    datos = datos.map(lambda r, yw: (_leer_jpeg(r), *yw), num_parallel_calls=tf.data.AUTOTUNE).batch(LOTE)
    if entrenamiento:
        aumentos = crear_aumentos()
        datos = datos.map(
            lambda x, y, w: (tf.clip_by_value(aumentos(x, training=True), 0.0, 255.0), y, w),
            num_parallel_calls=tf.data.AUTOTUNE,
        )
    return datos.prefetch(tf.data.AUTOTUNE)


# --- Modelo --------------------------------------------------------------------------------------------------------


def crear_modelo(pesos_base: str | None = "imagenet") -> Any:
    import keras

    base = keras.applications.MobileNetV3Small(
        input_shape=(LADO, LADO, 3), include_top=False, weights=pesos_base, include_preprocessing=True
    )
    base.trainable = False
    entrada = keras.Input((LADO, LADO, 3), name="imagen")
    # training=False: las capas de normalización por lotes de la base quedan en modo inferencia también en la fase 2.
    x = base(entrada, training=False)
    x = keras.layers.GlobalAveragePooling2D(name="promedio")(x)
    x = keras.layers.Dropout(0.2, name="dropout")(x)
    salidas = [keras.layers.Dense(1, activation="sigmoid", name=a)(x) for a in ATRIBUTOS]
    return keras.Model(entrada, salidas, name="clasificador_epp")


def base_del_modelo(modelo: Any) -> Any:
    return modelo.get_layer("MobileNetV3Small")


def compilar(modelo: Any, tasa: float) -> None:
    import keras

    modelo.compile(
        optimizer=keras.optimizers.Adam(tasa),
        loss=["binary_crossentropy"] * len(ATRIBUTOS),
        weighted_metrics=[[keras.metrics.BinaryAccuracy(name="exactitud")] for _ in ATRIBUTOS],
    )


@dataclass
class Entrenamiento:
    epocas_fase1: int = 10
    tasa_fase1: float = 1e-3
    # Fase 2 ajustada (01/11/2026): el diseño decía 10 épocas, Adam 1e-5 y 30 capas; con eso no llegaba a los mínimos.
    epocas_fase2: int = 20
    tasa_fase2: float = 1e-4
    capas_descongeladas: int = 60
    paciencia: int = 3
    historial: dict[str, list[float]] = field(default_factory=dict)
    segundos: float = 0.0


def entrenar(modelo: Any, entreno: Any, validacion: Any, plan: Entrenamiento, verbose: int = 2) -> Entrenamiento:
    """Fase 1: base congelada (Adam 1e-3). Fase 2: últimas capas de la base descongeladas con parada temprana por la
    pérdida de validación (se quedan los mejores pesos)."""
    import keras

    inicio = time.perf_counter()
    compilar(modelo, plan.tasa_fase1)
    h1 = modelo.fit(entreno, validation_data=validacion, epochs=plan.epocas_fase1, verbose=verbose)

    base = base_del_modelo(modelo)
    base.trainable = True
    for capa in base.layers[: -plan.capas_descongeladas]:
        capa.trainable = False
    compilar(modelo, plan.tasa_fase2)
    parada = keras.callbacks.EarlyStopping(monitor="val_loss", patience=plan.paciencia, restore_best_weights=True)
    h2 = modelo.fit(
        entreno,
        validation_data=validacion,
        epochs=plan.epocas_fase1 + plan.epocas_fase2,
        initial_epoch=plan.epocas_fase1,
        callbacks=[parada],
        verbose=verbose,
    )
    plan.historial = {k: [float(v) for v in h1.history[k]] + [float(v) for v in h2.history[k]] for k in h1.history}
    plan.segundos = time.perf_counter() - inicio
    return plan


def predecir(modelo: Any, datos: Particion | np.ndarray) -> dict[str, np.ndarray]:
    """Probabilidades de cada atributo para una partición (leída de disco) o para imágenes ya cargadas."""
    entrada = crear_dataset(datos) if isinstance(datos, Particion) else datos.astype(np.float32)
    salidas = modelo.predict(entrada, batch_size=LOTE, verbose=0)
    return {a: np.asarray(s, dtype=np.float32).reshape(-1) for a, s in zip(ATRIBUTOS, salidas, strict=True)}


# --- Métricas y umbrales -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MetricasAtributo:
    """Métricas de un atributo con la **falta** de EPP como clase positiva (``p < umbral`` → «sin»)."""

    umbral: float
    recortes: int
    con_epp: int
    sin_epp: int
    sensibilidad_falta: float
    precision_falta: float
    exactitud: float
    auc: float
    matriz: list[list[int]]  # filas: real [con, sin]; columnas: predicho [con, sin]


def medir(probabilidades: np.ndarray, etiquetas: np.ndarray, pesos: np.ndarray, umbral: float) -> MetricasAtributo:
    from sklearn.metrics import confusion_matrix, roc_auc_score

    decididos = pesos > 0
    falta_real = (etiquetas[decididos] == 0).astype(int)
    falta_predicha = (probabilidades[decididos] < umbral).astype(int)
    vp = int(((falta_real == 1) & (falta_predicha == 1)).sum())
    fp = int(((falta_real == 0) & (falta_predicha == 1)).sum())
    fn = int(((falta_real == 1) & (falta_predicha == 0)).sum())
    n = int(decididos.sum())
    matriz = confusion_matrix(1 - falta_real, 1 - falta_predicha, labels=[1, 0]).tolist()
    auc = float(roc_auc_score(falta_real, 1 - probabilidades[decididos])) if 0 < falta_real.sum() < n else float("nan")
    return MetricasAtributo(
        umbral=round(float(umbral), 2),
        recortes=n,
        con_epp=int((falta_real == 0).sum()),
        sin_epp=int(falta_real.sum()),
        sensibilidad_falta=vp / (vp + fn) if vp + fn else 0.0,
        precision_falta=vp / (vp + fp) if vp + fp else 0.0,
        exactitud=float((falta_real == falta_predicha).mean()) if n else 0.0,
        auc=auc,
        matriz=[[int(v) for v in fila] for fila in matriz],
    )


def elegir_umbral(probabilidades: np.ndarray, etiquetas: np.ndarray, pesos: np.ndarray) -> float:
    """Umbral elegido en **validación** (nunca en prueba), de 0,05 a 0,95 cada 0,01: entre los que dan sensibilidad de
    «sin EPP» ≥ 0,85 (se prioriza la sensibilidad), el de mayor precisión y, a igual precisión, el de mayor exactitud y
    el más cercano a 0,50. Si ninguno llega a 0,85, el de mayor sensibilidad con esas mismas preferencias."""
    candidatos = [round(float(u), 2) for u in np.arange(0.05, 0.951, 0.01)]
    medidas = [medir(probabilidades, etiquetas, pesos, u) for u in candidatos]
    llegan = [m for m in medidas if m.sensibilidad_falta >= MINIMOS["sensibilidad_falta"]]
    if llegan:
        mejor = max(llegan, key=lambda m: (m.precision_falta, m.exactitud, -abs(m.umbral - 0.5)))
    else:
        mejor = max(medidas, key=lambda m: (m.sensibilidad_falta, m.precision_falta, -abs(m.umbral - 0.5)))
    return mejor.umbral


def medir_por_fuente(
    particion: Particion, probabilidades: Mapping[str, np.ndarray], umbrales: Mapping[str, float]
) -> dict[str, dict[str, MetricasAtributo]]:
    """Las mismas métricas separadas por dataset de origen (solo los atributos que ese dataset permite decidir)."""
    resultado: dict[str, dict[str, MetricasAtributo]] = {}
    for fuente in sorted(set(particion.fuentes.tolist())):
        indices = particion.fuentes == fuente
        resultado[fuente] = {
            a: medir(
                probabilidades[a][indices], particion.etiquetas[a][indices], particion.pesos[a][indices], umbrales[a]
            )
            for a in ATRIBUTOS
            if particion.pesos[a][indices].sum() > 0
        }
    return resultado


def cumple(metricas: Mapping[str, MetricasAtributo], tamano_mb: float | None) -> list[str]:
    """Lista de métricas que no llegan al mínimo (vacía si cumple todo)."""
    faltas: list[str] = []
    for a, m in metricas.items():
        for nombre in ("sensibilidad_falta", "precision_falta", "exactitud"):
            valor = getattr(m, nombre)
            if valor < MINIMOS[nombre]:
                faltas.append(f"{a}.{nombre} = {valor:.3f} < {MINIMOS[nombre]}")
    if tamano_mb is not None and tamano_mb > MINIMOS["tamano_tflite_mb"]:
        faltas.append(f"tamaño de los dos .tflite = {tamano_mb:.2f} MB > {MINIMOS['tamano_tflite_mb']} MB")
    return faltas


def verificar_minimos(metricas: Mapping[str, MetricasAtributo], tamano_mb: float | None = None) -> None:
    faltas = cumple(metricas, tamano_mb)
    if faltas:
        raise MetricasInsuficientesError(
            "El modelo NO alcanza las métricas mínimas del diseño (no se incluye en la app): " + "; ".join(faltas)
        )


def guardar_matriz_confusion(metricas: Mapping[str, MetricasAtributo], ruta: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figura, ejes = plt.subplots(1, len(metricas), figsize=(4.2 * len(metricas), 3.8))
    for eje, (a, m) in zip(np.atleast_1d(ejes), metricas.items(), strict=True):
        datos = np.array(m.matriz)
        eje.imshow(datos, cmap="Blues")
        for i in range(2):
            for j in range(2):
                color = "white" if datos[i, j] > datos.max() / 2 else "black"
                eje.text(j, i, str(datos[i, j]), ha="center", va="center", color=color, fontsize=13)
        eje.set_xticks([0, 1], [f"con {a}", f"sin {a}"])
        eje.set_yticks([0, 1], [f"con {a}", f"sin {a}"])
        eje.set_xlabel("Predicho")
        eje.set_ylabel("Real")
        eje.set_title(f"{a} (umbral {m.umbral:.2f})")
    figura.suptitle("Matriz de confusión · 15 % de prueba")
    figura.tight_layout()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    figura.savefig(ruta, dpi=110)
    plt.close(figura)


# --- Exportación ---------------------------------------------------------------------------------------------------


def modelo_para_exportar(modelo: Any) -> Any:
    """Mismo modelo con una sola salida ``(N, 2)`` = ``[p_casco, p_chaleco]``: el orden de varias salidas en TFLite
    no está garantizado, una sola salida con orden fijo (``epp.json`` → ``salidas``) es más segura para la app
    (decisión técnica)."""
    import keras

    unidas = keras.layers.Concatenate(name="epp")(list(modelo.outputs))
    return keras.Model(modelo.inputs, unidas, name="clasificador_epp_tflite")


def exportar_tflite(modelo: Any, carpeta: Path = CARPETA_SALIDA) -> Path:
    """``model.export(saved_model)`` y conversión a TFLite con cuantización float16."""
    import tensorflow as tf

    carpeta.mkdir(parents=True, exist_ok=True)
    saved_model = carpeta / "saved_model"
    modelo_para_exportar(modelo).export(str(saved_model), verbose=False)
    convertidor = tf.lite.TFLiteConverter.from_saved_model(str(saved_model))
    convertidor.optimizations = [tf.lite.Optimize.DEFAULT]
    convertidor.target_spec.supported_types = [tf.float16]
    ruta = carpeta / "clasificador_epp.tflite"
    ruta.write_bytes(convertidor.convert())
    return ruta


def predecir_tflite(ruta: Path, imagenes: np.ndarray) -> np.ndarray:
    """Predicciones del ``.tflite`` de a una imagen (como en el celular): ``(N, 2)``."""
    import tensorflow as tf

    interprete = tf.lite.Interpreter(model_path=str(ruta))
    entrada = interprete.get_input_details()[0]
    salida = interprete.get_output_details()[0]
    interprete.resize_tensor_input(entrada["index"], [1, LADO, LADO, 3])
    interprete.allocate_tensors()
    resultados = np.zeros((len(imagenes), len(ATRIBUTOS)), dtype=np.float32)
    for i, imagen in enumerate(imagenes):
        interprete.set_tensor(entrada["index"], imagen[None].astype(np.float32))
        interprete.invoke()
        resultados[i] = interprete.get_tensor(salida["index"])[0]
    return resultados


def comprobar_equivalencia(modelo: Any, ruta: Path, imagenes: np.ndarray) -> float:
    """Diferencia máxima entre Keras y TFLite en las primeras 50 imágenes; error si pasa de 0,02."""
    muestra = imagenes[:RECORTES_EQUIVALENCIA]
    keras_pred = np.stack([predecir(modelo, muestra)[a] for a in ATRIBUTOS], axis=1)
    tflite_pred = predecir_tflite(ruta, muestra)
    diferencia = float(np.abs(keras_pred - tflite_pred).max())
    if diferencia > DIFERENCIA_MAXIMA_TFLITE:
        raise EquivalenciaTfliteError(
            f"El .tflite difiere del modelo de Keras en {diferencia:.4f} (máximo {DIFERENCIA_MAXIMA_TFLITE})."
        )
    return diferencia


def guardar_configuracion_app(umbrales: Mapping[str, float], carpeta: Path = CARPETA_SALIDA) -> Path:
    """``epp.json`` para la app: versión, umbrales y tamaño de entrada (diseño, «Flujo en la app»)."""
    configuracion = {
        "version": VERSION,
        "versionModelo": f"epp-{VERSION}",
        "entrada": {"alto": LADO, "ancho": LADO, "canales": 3, "tipo": "float32", "rango": [0, 255]},
        "salidas": list(ATRIBUTOS),
        "umbrales": {a: round(float(umbrales[a]), 2) for a in ATRIBUTOS},
    }
    ruta = carpeta / "epp.json"
    ruta.write_text(json.dumps(configuracion, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ruta


def guardar_metricas(
    prueba: Mapping[str, MetricasAtributo],
    validacion: Mapping[str, MetricasAtributo],
    *,
    plan: Entrenamiento,
    resumen_recortes: Mapping[str, Any],
    tamanos_mb: Mapping[str, float],
    diferencia_tflite: float | None,
    prueba_por_fuente: Mapping[str, Mapping[str, MetricasAtributo]] | None = None,
    carpeta: Path = CARPETA_SALIDA,
) -> Path:
    tamano_total = sum(tamanos_mb.values()) if tamanos_mb else None
    faltas = cumple(prueba, tamano_total if len(tamanos_mb) == 2 else None)
    datos = {
        "version": VERSION,
        "versionModelo": f"epp-{VERSION}",
        "fecha": datetime.now(UTC).isoformat(timespec="seconds"),
        "datasets": [DATASETS[f] for f in resumen_recortes.get("fuentes", ["gsnvb"])],
        "clasePositiva": "falta de EPP (sin casco, sin chaleco)",
        "minimos": MINIMOS,
        "cumpleMinimos": not faltas,
        "faltas": faltas,
        "prueba": {a: asdict(m) for a, m in prueba.items()},
        "pruebaPorDataset": {
            f: {a: asdict(m) for a, m in por_atributo.items()} for f, por_atributo in (prueba_por_fuente or {}).items()
        },
        "validacion": {a: asdict(m) for a, m in validacion.items()},
        "tamanosMb": {k: round(v, 3) for k, v in tamanos_mb.items()},
        "diferenciaMaximaKerasTflite": diferencia_tflite,
        "entrenamiento": {
            "epocasFase1": plan.epocas_fase1,
            "tasaFase1": plan.tasa_fase1,
            "tasaFase2": plan.tasa_fase2,
            "epocasFase2Maximas": plan.epocas_fase2,
            "epocasCorridas": len(plan.historial.get("loss", [])),
            "capasDescongeladas": plan.capas_descongeladas,
            "segundos": round(plan.segundos, 1),
        },
        "recortes": resumen_recortes,
    }
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "metricas.json"
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ruta


def tamano_mb(ruta: Path) -> float:
    return ruta.stat().st_size / (1024 * 1024)


def main(argv: list[str] | None = None) -> None:
    import argparse

    import keras

    defecto = Entrenamiento()
    parser = argparse.ArgumentParser(description="Entrena, evalúa y exporta el clasificador de casco y chaleco.")
    parser.add_argument("--epocas-fase1", type=int, default=defecto.epocas_fase1)
    parser.add_argument("--epocas-fase2", type=int, default=defecto.epocas_fase2)
    parser.add_argument("--tasa-fase2", type=float, default=defecto.tasa_fase2)
    parser.add_argument("--capas-descongeladas", type=int, default=defecto.capas_descongeladas)
    args = parser.parse_args(argv)
    configurar_hilos()
    keras.utils.set_random_seed(SEMILLA)
    tabla = leer_recortes()
    resumen = json.loads((CARPETA_RECORTES / "resumen.json").read_text(encoding="utf-8"))
    entreno, validacion, prueba = (cargar_particion(tabla, d) for d in ("entrenamiento", "validacion", "prueba"))
    modelo = crear_modelo()
    plan = entrenar(
        modelo,
        crear_dataset(entreno, pesos_balanceados(entreno), entrenamiento=True),
        crear_dataset(validacion, pesos_balanceados(validacion), entrenamiento=False),
        Entrenamiento(
            epocas_fase1=args.epocas_fase1,
            epocas_fase2=args.epocas_fase2,
            tasa_fase2=args.tasa_fase2,
            capas_descongeladas=args.capas_descongeladas,
        ),
    )
    modelo.save(CARPETA_SALIDA / "clasificador_epp.keras")
    p_val, p_prueba = predecir(modelo, validacion), predecir(modelo, prueba)
    umbrales = {a: elegir_umbral(p_val[a], validacion.etiquetas[a], validacion.pesos[a]) for a in ATRIBUTOS}
    m_val = {a: medir(p_val[a], validacion.etiquetas[a], validacion.pesos[a], umbrales[a]) for a in ATRIBUTOS}
    m_prueba = {a: medir(p_prueba[a], prueba.etiquetas[a], prueba.pesos[a], umbrales[a]) for a in ATRIBUTOS}
    m_fuentes = medir_por_fuente(prueba, p_prueba, umbrales)
    for a in ATRIBUTOS:
        print(a, "validación", m_val[a])
        print(a, "prueba", m_prueba[a])
        for fuente, por_atributo in m_fuentes.items():
            if a in por_atributo:
                print(a, "prueba", fuente, por_atributo[a])
    ruta = exportar_tflite(modelo)
    diferencia = comprobar_equivalencia(modelo, ruta, prueba.imagenes(range(RECORTES_EQUIVALENCIA)))
    tamanos = {"clasificador_epp.tflite": tamano_mb(ruta)}
    detector = CARPETA_EPP / "datos" / "efficientdet_lite0.tflite"
    if detector.exists():
        tamanos["efficientdet_lite0.tflite"] = tamano_mb(detector)
    guardar_configuracion_app(umbrales)
    guardar_metricas(
        m_prueba,
        m_val,
        plan=plan,
        resumen_recortes=resumen,
        tamanos_mb=tamanos,
        diferencia_tflite=diferencia,
        prueba_por_fuente=m_fuentes,
    )
    guardar_matriz_confusion(m_prueba, CARPETA_SALIDA / "matriz_confusion.png")
    print(f"Diferencia máxima Keras/TFLite: {diferencia:.5f}; tamaños: {tamanos}; {plan.segundos:.0f} s")
    verificar_minimos(m_prueba, sum(tamanos.values()) if len(tamanos) == 2 else None)


if __name__ == "__main__":
    main()
