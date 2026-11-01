# Clasificador de casco y chaleco (301)

Entrenamiento del modelo de *deep learning* de la plataforma (punto 12): un clasificador **MobileNetV3-Small** con ajuste fino (TensorFlow / Keras) que, para cada persona que detecta el celular, da la probabilidad de que lleve **casco** y **chaleco**. Corre en la app de obra como TensorFlow Lite, después del detector de personas EfficientDet-Lite0 (preentrenado, no se reentrena). Requisitos y diseño: `docs/specs/301-deteccion-epp/` de `supervision-docs`.

## Dataset

**Construction Safety** de Roboflow 100 (`construction-safety-gsnvb`), versión «v1 release» exportada de Roboflow el 25/07/2022.

| | |
|---|---|
| Página original | https://universe.roboflow.com/object-detection/construction-safety-gsnvb |
| Descarga (sin cuenta ni claves) | Hugging Face, `Francesco/construction-safety-gsnvb` (subido por Francesco Zuppichini, uno de los autores de Roboflow 100), archivo `dataset.tar.gz` (COCO, 75 MB), revisión `cd92c83884cc7c0ba1a3fda584784f550321e16f`, SHA-256 `70e7b0c3…13842763` |
| Licencia | **CC BY 4.0** (`README.roboflow.txt` del propio dataset: «License: CC BY 4.0», y `dataset_info.json` de Hugging Face). Permite el uso académico (y cualquier otro) citando la fuente. Revisada el 01/11/2026 |
| Imágenes | 1 206 (997 / 119 / 90 en las partes del autor; aquí se vuelven a dividir 70 / 15 / 15), redimensionadas a 640 × 640 (estiradas) por Roboflow, sin aumentos |
| Clases | `person` (2 817 cajas), `helmet` (2 543), `no-helmet` (129), `vest` (1 343), `no-vest` (892); `construction-safety` es la supercategoría, sin cajas |

Cita:

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
```

Por qué este (decisión técnica, 01/11/2026): tiene justo las clases del diseño (persona, casco, chaleco y sus negativos «sin casco» y «sin chaleco»), una licencia clara que permite el uso académico y se descarga sin crear cuentas ni usar claves. Candidatos revisados y descartados:

- *Construction Site Safety Image Dataset* (Kaggle, exportado de Roboflow): Kaggle pide cuenta (o clave de API) para descargar. En Hugging Face solo está una versión de 398 imágenes (`keremberke/construction-safety-object-detection`).
- *SH17* (GitHub `ahmadmughees/SH17dataset`, CC BY-NC-SA 4.0, 8 099 imágenes): el repositorio solo trae la lista de URL de Pexels; las etiquetas están en Kaggle (pide cuenta). La copia de Hugging Face (`fathansanum/SH-17-Dataset`) es una reexportación de terceros que dice «CC BY 4.0», distinta de la licencia original: no se usa.
- Roboflow Universe directamente: pide cuenta o clave de API para descargar.

Limitación conocida: el dataset no está anotado por completo (no todas las personas tienen su casco/chaleco o su negativo) y hay pocas personas «sin casco» (129 cajas `no-helmet`). Por eso lo que no se puede decidir lleva peso 0 y no se toma la falta de una caja de chaleco como «sin chaleco».

### Descargar

Desde `services/ia`, con el entorno de entrenamiento (ver «Entorno»):

```powershell
entrenamiento\epp\.venv\Scripts\python -m entrenamiento.epp.descargar_dataset
```

Baja el archivo de la revisión fija, comprueba su SHA-256 y lo extrae en `entrenamiento/epp/datos/gsnvb/{train,valid,test}/` (con `_annotations.coco.json` en cada parte). `datos/` no se versiona.

## Entorno

Aparte del servicio (el `pyproject.toml` del servicio no instala TensorFlow): `entrenamiento/epp/.venv` con `requirements-entrenamiento.txt`. Se crea con el `uv` y el Python 3.12 que ya usa el servicio (ver el README del servicio); desde `services/ia`:

```powershell
uv venv entrenamiento\epp\.venv --python 3.12
uv pip install --python entrenamiento\epp\.venv\Scripts\python.exe -r entrenamiento\epp\requirements-entrenamiento.txt
entrenamiento\epp\.venv\Scripts\python -c "import tensorflow as tf, keras; print(tf.__version__, keras.__version__)"
```

Comprobado el 01/11/2026 en la computadora del equipo (Windows 11, Intel Core i7-11370H, 16 GB, sin GPU): Python 3.12.16, **TensorFlow 2.21.0** (rueda `cp312-win_amd64` de PyPI, la versión del diseño), Keras 3.15.1, NumPy 2.5.4, pandas 3.1.0, scikit-learn 1.9.1, Matplotlib 3.11.2 y Pillow 13.0.0. En Windows nativo TensorFlow corre solo en CPU (lo avisa al importarse); alcanza para MobileNetV3-Small sobre recortes. `EPP_HILOS` (4 por defecto) limita los hilos de TensorFlow para no tomar toda la máquina.
