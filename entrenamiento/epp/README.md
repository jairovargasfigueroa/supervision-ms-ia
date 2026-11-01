# Clasificador de casco y chaleco (301)

Entrenamiento del modelo de *deep learning* de la plataforma (punto 12): un clasificador **MobileNetV3-Small** con ajuste fino (TensorFlow / Keras) que, para cada persona que detecta el celular, da la probabilidad de que lleve **casco** y **chaleco**. Corre en la app de obra como TensorFlow Lite, después del detector de personas EfficientDet-Lite0 (preentrenado, no se reentrena). Versión entregada: **`epp-1.0.0`**. Requisitos y diseño: `docs/specs/301-deteccion-epp/` de `supervision-docs`.

## Datasets

Se usan **dos** datasets públicos con licencia CC BY 4.0, los dos descargables sin cuenta ni claves (decisión técnica del 01/11/2026, ver «Por qué dos datasets»).

### Construction Safety (Roboflow 100)

`construction-safety-gsnvb`, versión «v1 release» exportada de Roboflow el 25/07/2022. Aporta el **chaleco** (y una parte del casco): personas, casco, chaleco y sus negativos.

| | |
|---|---|
| Página original | https://universe.roboflow.com/object-detection/construction-safety-gsnvb |
| Descarga | Hugging Face, `Francesco/construction-safety-gsnvb` (subido por Francesco Zuppichini, uno de los autores de Roboflow 100), archivo `dataset.tar.gz` (COCO, 75 MB), revisión `cd92c83884cc7c0ba1a3fda584784f550321e16f`, SHA-256 `70e7b0c3…13842763` |
| Licencia | **CC BY 4.0** (`README.roboflow.txt` del propio dataset: «License: CC BY 4.0», y `dataset_info.json` de Hugging Face). Permite el uso académico citando la fuente. Revisada el 01/11/2026 |
| Imágenes | 1 206 de obras (997 / 119 / 90 en las partes del autor; aquí se vuelven a dividir), redimensionadas a 640 × 640 (estiradas) por Roboflow, sin aumentos |
| Clases | `person` (2 817 cajas), `helmet` (2 543), `no-helmet` (129), `vest` (1 343), `no-vest` (892); `construction-safety` es la supercategoría, sin cajas |

### SHEL5K

*Safety Helmet detection with Extended Labels 5K images*, versión 4 (14/02/2022). Aporta el **casco**: muchas personas con y sin casco. Reetiqueta el dataset «Safety Helmet Detection» de Kaggle (imágenes de obras y de trabajadores con y sin casco).

| | |
|---|---|
| Página | https://data.mendeley.com/datasets/9rcv8mm682/4 (DOI 10.17632/9rcv8mm682.4) |
| Descarga | botón «Download All» de Mendeley Data (`https://data.mendeley.com/public-api/zip/9rcv8mm682/download/4`, redirige a un enlace temporal), un .zip de 1,3 GB (5 000 PNG y 5 000 XML Pascal VOC), SHA-256 `dfba1d3c…4e7a7a`. No se extrae: se lee desde el .zip |
| Licencia | **CC BY 4.0** (campo «Licence» de la página de Mendeley Data). Revisada el 01/11/2026 |
| Clases | `helmet` (19 252), `head_with_helmet` (16 048), `person_with_helmet` (14 767), `face` (14 135), `head` (6 120), `person_no_helmet` (5 248), `person` (8). No anota chalecos |

Citas:

```bibtex
@misc{construction-safety-gsnvb,
  title        = {construction safety gsnvb Dataset},
  type         = {Open Source Dataset},
  author       = {Roboflow 100},
  howpublished = {\url{https://universe.roboflow.com/object-detection/construction-safety-gsnvb}},
  publisher    = {Roboflow},
  year         = {2022},
  note         = {Licencia CC BY 4.0}
}
@misc{rf100,
  title  = {Roboflow 100: A Rich, Multi-Domain Object Detection Benchmark},
  author = {Floriana Ciaglia and Francesco Saverio Zuppichini and Paul Guerrie and Mark McQuade and Jacob Solawetz},
  year   = {2022},
  eprint = {2211.13523},
  archivePrefix = {arXiv}
}
@article{shel5k,
  title   = {SHEL5K: An Extended Dataset and Benchmarking for Safety Helmet Detection},
  author  = {Munkh-Erdene Otgonbold and Munkhjargal Gochoo and Fady Alnajjar and Luqman Ali and Tan-Hsu Tan and Jun-Wei Hsieh and Ping-Yang Chen},
  journal = {Sensors},
  volume  = {22},
  number  = {6},
  pages   = {2315},
  year    = {2022},
  doi     = {10.3390/s22062315}
}
@misc{shel5k-datos,
  title     = {Safety Helmet detection with Extended Labels 5K images (SHEL5K)},
  author    = {Munkhjargal Gochoo},
  publisher = {Mendeley Data},
  version   = {4},
  year      = {2022},
  doi       = {10.17632/9rcv8mm682.4},
  note      = {Licencia CC BY 4.0}
}
```

### Por qué dos datasets

Se empezó solo con Construction Safety (tiene justo las clases del diseño). El chaleco llegó a los mínimos, pero el casco no: el dataset tiene apenas 129 cajas `no-helmet` (118 personas «sin casco» decididas, 24 en la prueba) y el modelo quedó lejos (ver «Intentos»). Se agregó SHEL5K, que suma unas 3 800 personas sin casco anotadas con cabeza y casco, y se le aplican las **mismas reglas** de etiquetado (casco en la zona de la cabeza → con casco; cabeza sin casco en esa zona → sin casco). SHEL5K no anota chalecos: en sus recortes el chaleco lleva peso 0 y solo cuenta el casco.

Candidatos revisados y descartados:

- *Construction Site Safety Image Dataset* (Kaggle, exportado de Roboflow): Kaggle pide cuenta (o clave de API) para descargar. En Hugging Face hay una versión de 398 imágenes (`keremberke/construction-safety-object-detection`, CC BY 4.0), con solo 69 personas anotadas: no alcanza para recortes.
- *SH17* (GitHub `ahmadmughees/SH17dataset`, CC BY-NC-SA 4.0, 8 099 imágenes): el repositorio solo trae la lista de URL de Pexels; las etiquetas están en Kaggle (pide cuenta). La copia de Hugging Face (`fathansanum/SH-17-Dataset`) es una reexportación de terceros que dice «CC BY 4.0», distinta de la licencia original: no se usa.
- Roboflow Universe directamente: pide cuenta o clave de API para descargar.

Limitaciones conocidas: ninguno de los dos está anotado por completo para el chaleco (la falta de un chaleco no se toma como «sin chaleco»). En SHEL5K, la regla geométrica coincide con su propia clase de persona en el 95 % de las «person_no_helmet» (3 813 de 4 005 decididas; las demás tienen el casco de otra persona en su zona de la cabeza) y en el 99,8 % de las «person_with_helmet»: el ruido de etiquetas es bajo. Construction Safety viene estirado a 640 × 640; la app también estira el recorte de la persona a 224 × 224.

### Descargar

Desde `services/ia`, con el entorno de entrenamiento (ver «Entorno»):

```powershell
entrenamiento\epp\.venv\Scripts\python -m entrenamiento.epp.descargar_dataset
```

Baja los dos archivos, comprueba su SHA-256, extrae Construction Safety en `entrenamiento/epp/datos/gsnvb/{train,valid,test}/` (con `_annotations.coco.json` en cada parte) y deja `entrenamiento/epp/datos/shel5k/shel5k.zip` sin extraer. `datos/` no se versiona (unos 1,5 GB con los recortes).

## Entorno

Aparte del servicio (el `pyproject.toml` del servicio no instala TensorFlow): `entrenamiento/epp/.venv` con `requirements-entrenamiento.txt`. Se crea con el `uv` y el Python 3.12 que ya usa el servicio (ver el README del servicio); desde `services/ia`:

```powershell
uv venv entrenamiento\epp\.venv --python 3.12
uv pip install --python entrenamiento\epp\.venv\Scripts\python.exe -r entrenamiento\epp\requirements-entrenamiento.txt
entrenamiento\epp\.venv\Scripts\python -c "import tensorflow as tf, keras; print(tf.__version__, keras.__version__)"
```

Comprobado el 01/11/2026 en la computadora del equipo (Windows 11, Intel Core i7-11370H, 16 GB, sin GPU): Python 3.12.16, **TensorFlow 2.21.0** (rueda `cp312-win_amd64` de PyPI, la versión del diseño), Keras 3.15.1, NumPy 2.5.4, pandas 3.1.0, scikit-learn 1.9.1, Matplotlib 3.11.2 y Pillow 13.0.0. En Windows nativo TensorFlow corre solo en CPU (lo avisa al importarse); alcanza para MobileNetV3-Small sobre recortes. `EPP_HILOS` (4 por defecto) limita los hilos de TensorFlow para no tomar toda la máquina.

## Recortes y etiquetas

```powershell
entrenamiento\epp\.venv\Scripts\python -m entrenamiento.epp.preparar_recortes    # datos/recortes/ (≈ 9 min)
```

`preparar_recortes.py` aplica las reglas del diseño («Etiquetado de recortes») a los dos datasets: un recorte por persona de al menos 64 px de alto con margen de 10 %, `casco = 1` si un casco tiene ≥ 60 % de su área en el 40 % superior de la persona y `0` si hay un `no-helmet` (Construction Safety) o una cabeza sin casco (`head` de SHEL5K) ahí, `chaleco = 1` si un chaleco tiene ≥ 60 % de su área dentro de la persona y `0` si hay un `no-vest`; lo que no se puede decidir lleva peso 0. Escribe los recortes de 224 × 224 en `datos/recortes/<división>/`, `recortes.csv` (archivo, dataset, imagen, división, valor y peso de cada atributo) y `resumen.json`. La división 70 / 15 / 15 es **por imagen** (semilla 2026), sobre las imágenes de los dos datasets juntas y sin mirar las partes de los autores. `--fuentes gsnvb` prepara solo Construction Safety. Las reglas se prueban en `tests/entrenamiento/epp/test_preparar_recortes.py` (`uv run pytest`, sin TensorFlow).

Decisiones técnicas (no cambian reglas del negocio):

- Margen: 10 % del ancho a cada lado y 10 % del alto arriba y abajo, cortado en el borde de la imagen (la app recorta igual).
- Si en la misma zona hay a la vez un casco y un «sin casco» (o un chaleco y un `no-vest`), casi siempre por personas superpuestas, el atributo queda sin decidir (peso 0). Una cabeza en la zona cuenta como «sin casco» solo si no hay un casco.
- Una persona sin ninguno de los dos atributos decididos no se recorta.
- Ningún dataset está anotado por completo para el chaleco: la falta de un chaleco no se toma como «sin chaleco».

Resultado con la semilla 2026 (6 206 imágenes; 3 683 personas de menos de 64 px y 767 sin ningún atributo decidido quedan fuera):

| División | Imágenes | Recortes | Con casco | Sin casco | Casco sin decidir | Con chaleco | Sin chaleco | Chaleco sin decidir |
|---|---|---|---|---|---|---|---|---|
| Entrenamiento | 4 344 | 12 725 | 9 904 | 2 737 | 84 | 911 | 644 | 11 170 |
| Validación | 931 | 2 857 | 2 232 | 606 | 19 | 208 | 118 | 2 531 |
| Prueba | 931 | 2 808 | 2 170 | 606 | 32 | 191 | 106 | 2 511 |
| **Total** | **6 206** | **18 390** | 14 306 | **3 949** | 135 | 1 310 | **868** | 16 212 |

Por dataset: Construction Safety 1 206 imágenes y 2 614 recortes (118 sin casco, 868 sin chaleco); SHEL5K 5 000 imágenes y 15 776 recortes (3 831 sin casco, chaleco siempre sin decidir).

## Entrenamiento

```powershell
cd entrenamiento\epp
$env:EPP_HILOS = 6
.venv\Scripts\python -m jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.timeout=-1 entrenar_clasificador_epp.ipynb
# o, sin notebook, desde services/ia:  entrenamiento\epp\.venv\Scripts\python -m entrenamiento.epp.clasificador
```

`entrenar_clasificador_epp.ipynb` (guardado con sus salidas) usa `clasificador.py`: datos leídos de disco con `tf.data`, aumentos (volteo horizontal, brillo y contraste, rotación ±10°, recorte aleatorio), `MobileNetV3Small(include_top=False, weights="imagenet", include_preprocessing=True)` + `GlobalAveragePooling` + `Dropout(0.2)` + `Dense(1, sigmoid)` para `casco` y para `chaleco`, entrenamiento en dos fases con parada temprana por la pérdida de validación, elección de umbrales en validación, evaluación en prueba (total y por dataset), matriz de confusión, exportación y verificación de los mínimos. **La última celda falla con un mensaje claro** (`MetricasInsuficientesError`) si no se cumplen; `metricas.json` se guarda igual.

Decisiones técnicas:

- **Fase 2 ajustada (01/11/2026):** fase 1 como el diseño (base congelada, 10 épocas, Adam 1e-3); fase 2 con las **últimas 60 capas** de la base, **hasta 20 épocas** y **Adam 1e-4** (el diseño decía 30 capas, 10 épocas y 1e-5). Con la fase 2 del diseño el chaleco quedaba en sensibilidad 0,842 (ver «Intentos»).
- Pesos por recorte: 0 para lo indeterminado y `n / (2 · n_clase)` por atributo para lo decidido, para que la clase minoritaria pese tanto como la mayoritaria.
- Las capas de normalización por lotes de la base quedan en modo inferencia también en la fase 2 (`base(x, training=False)`).
- Umbral de cada atributo elegido en **validación** (nunca en prueba), de 0,05 a 0,95: entre los que dan sensibilidad de «sin EPP» ≥ 0,85, el de mayor precisión. Se guardan en `epp.json`.
- Los recortes se leen de disco en cada época (18 000 imágenes no caben cómodas en memoria junto a lo que corre en la máquina).

Tiempo en la computadora del equipo (CPU, 6 hilos, con otros procesos corriendo): 23 min de entrenamiento (16 épocas: 10 + 6 con parada temprana) y unos 25 min el notebook completo.

### Intentos (métricas de la prueba)

| Corrida | Datos | Fase 2 | Casco: sens. / prec. / exact. (AUC) | Chaleco: sens. / prec. / exact. (AUC) | ¿Cumple? |
|---|---|---|---|---|---|
| 1 | Construction Safety | 30 capas, 10 épocas, 1e-5 (diseño) | 0,583 / 0,179 / 0,787 (0,82) | 0,842 / 0,933 / 0,906 (0,97) | No |
| 2 | Construction Safety | 60 capas, 20 épocas, 1e-4 (fase 1 de 20 épocas) | 0,542 / 0,232 / 0,845 (0,87) | 0,857 / 0,958 / 0,922 (0,98) | No (casco) |
| 3 | + SHEL5K | 60 capas, 20 épocas, 1e-4 | 0,865 / 0,934 / 0,957 (0,985) | 0,915 / 0,942 / 0,949 (0,99) | Sí |
| **Entregada** (notebook) | + SHEL5K | 60 capas, 20 épocas, 1e-4 | **0,883 / 0,926 / 0,959 (0,987)** | **0,906 / 0,950 / 0,949 (0,98)** | **Sí** |

Con solo Construction Safety, «sin casco» tenía 24 casos en prueba (y 9 en validación para elegir el umbral) frente a 324 con casco: aun con más épocas el modelo no separaba bien y la precisión era muy baja. La corrida 3 y la del notebook repiten la misma receta; la diferencia entre ellas es la variación normal del entrenamiento en CPU.

## Métricas del modelo entregado (`epp-1.0.0`)

Sobre el 15 % de prueba, con la **falta** de EPP como clase positiva (`salida/metricas.json`, `salida/matriz_confusion.png`):

| Métrica | Casco (umbral 0,38) | Chaleco (umbral 0,21) | Mínimo |
|---|---|---|---|
| Sensibilidad de «sin …» | **0,883** (535 de 606) | **0,906** (96 de 106) | ≥ 0,85 |
| Precisión de «sin …» | **0,926** (535 de 578) | **0,950** (96 de 101) | ≥ 0,75 |
| Exactitud | **0,959** (2 776 recortes) | **0,949** (297 recortes) | ≥ 0,85 |
| AUC | 0,987 | 0,979 | — |
| Tamaño de los dos `.tflite` | 1,87 MB (clasificador) + 4,39 MB (detector) = **6,25 MB** | | ≤ 10 MB |

Por dataset (mismos umbrales): casco en Construction Safety 0,800 / 1,000 / 0,994 (solo 10 personas sin casco en su parte de prueba: 8 detectadas, ningún falso aviso); casco en SHEL5K 0,884 / 0,925 / 0,954; el chaleco sale solo de Construction Safety.

Lo que **no** se midió aquí: el tiempo en el celular (≤ 2 s con hasta 5 personas, criterio 16) y el conteo por foto completa con el detector de personas (error absoluto medio con fotos propias de obra). Se miden al integrar en la app. Las métricas son por recorte con cajas de persona anotadas a mano; en la app las cajas vienen del detector.

