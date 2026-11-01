"""Descarga los datasets públicos de EPP en ``datos/`` (301), sin cuentas ni claves.

- Construction Safety (Roboflow 100, ``construction-safety-gsnvb``), CC BY 4.0, publicado en Hugging Face por uno de
  los autores de Roboflow 100: personas, casco, chaleco y sus negativos. Se extrae con el filtro ``data`` de
  ``tarfile`` (sin rutas absolutas ni enlaces fuera de la carpeta).
- SHEL5K (Mendeley Data, doi:10.17632/9rcv8mm682.4), CC BY 4.0: personas con y sin casco, cabezas y cascos. Se deja
  el .zip sin extraer (1,3 GB de PNG): ``preparar_recortes`` lo lee directamente.

Cada archivo se comprueba con su SHA-256.

Uso (desde ``services/ia``)::

    entrenamiento/epp/.venv/Scripts/python -m entrenamiento.epp.descargar_dataset
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tarfile
import urllib.request
from pathlib import Path

CARPETA_EPP = Path(__file__).resolve().parent
CARPETA_DATOS = CARPETA_EPP / "datos"

# Revisión fija del repositorio de Hugging Face (la misma que se usó para entrenar).
REVISION_GSNVB = "cd92c83884cc7c0ba1a3fda584784f550321e16f"
URL_GSNVB = (
    f"https://huggingface.co/datasets/Francesco/construction-safety-gsnvb/resolve/{REVISION_GSNVB}/dataset.tar.gz"
)
SHA256_GSNVB = "70e7b0c3f597f8e6b45e41fb22259de96feba8d763a93305a273384b13842763"
PARTES_GSNVB = ("train", "valid", "test")

# Versión 4 de SHEL5K en Mendeley Data (la descarga pública redirige a un enlace temporal de S3).
URL_SHEL5K = "https://data.mendeley.com/public-api/zip/9rcv8mm682/download/4"
SHA256_SHEL5K = "dfba1d3ce01af69d791020cdfdfdbc25904b41724d11160361e7a4cd164e7a7a"


def _sha256(archivo: Path) -> str:
    h = hashlib.sha256()
    with archivo.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _bajar(url: str, archivo: Path, sha256: str) -> None:
    if archivo.exists() and _sha256(archivo) == sha256:
        return
    archivo.parent.mkdir(parents=True, exist_ok=True)
    print(f"Descargando {url} …")
    with urllib.request.urlopen(url, timeout=120) as respuesta, archivo.open("wb") as salida:
        shutil.copyfileobj(respuesta, salida, 1 << 20)
    if _sha256(archivo) != sha256:
        raise SystemExit(f"El SHA-256 de {archivo} no coincide con el esperado: bórralo y vuelve a descargar.")


def descargar_gsnvb(destino: Path = CARPETA_DATOS) -> Path:
    """Descarga y extrae Construction Safety; devuelve la carpeta con ``train/``, ``valid/`` y ``test/`` (COCO)."""
    carpeta = destino / "gsnvb"
    if all((carpeta / parte / "_annotations.coco.json").exists() for parte in PARTES_GSNVB):
        print(f"Construction Safety ya está en {carpeta}")
        return carpeta
    archivo = carpeta / "dataset.tar.gz"
    _bajar(URL_GSNVB, archivo, SHA256_GSNVB)
    extraido = carpeta / "_extraido"
    shutil.rmtree(extraido, ignore_errors=True)
    with tarfile.open(archivo, "r:gz") as tar:
        tar.extractall(extraido, filter="data")
    # El archivo trae la ruta de quien lo armó (…/rf100/construction-safety-gsnvb/{train,valid,test}).
    anotaciones = sorted(extraido.rglob("_annotations.coco.json"))
    if len(anotaciones) != len(PARTES_GSNVB):
        raise SystemExit(f"Se esperaban {len(PARTES_GSNVB)} archivos _annotations.coco.json y hay {len(anotaciones)}.")
    raiz = anotaciones[0].parent.parent
    for parte in PARTES_GSNVB:
        shutil.rmtree(carpeta / parte, ignore_errors=True)
        shutil.move(str(raiz / parte), str(carpeta / parte))
    for leeme in raiz.glob("README*.txt"):
        shutil.move(str(leeme), str(carpeta / leeme.name))
    shutil.rmtree(extraido)
    print(f"Construction Safety listo en {carpeta}")
    return carpeta


def descargar_shel5k(destino: Path = CARPETA_DATOS) -> Path:
    """Descarga el .zip de SHEL5K (no se extrae)."""
    archivo = destino / "shel5k" / "shel5k.zip"
    _bajar(URL_SHEL5K, archivo, SHA256_SHEL5K)
    print(f"SHEL5K listo en {archivo}")
    return archivo


def main() -> None:
    parser = argparse.ArgumentParser(description="Descarga los datasets públicos de EPP (301).")
    parser.add_argument("--destino", type=Path, default=CARPETA_DATOS)
    args = parser.parse_args()
    descargar_gsnvb(args.destino)
    descargar_shel5k(args.destino)


if __name__ == "__main__":
    main()
