"""Descarga el dataset público de EPP en ``datos/`` (301).

Construction Safety (Roboflow 100, ``construction-safety-gsnvb``), CC BY 4.0, publicado en Hugging Face por uno de los
autores de Roboflow 100. Se baja sin cuenta ni claves, se comprueba su SHA-256 y se extrae con el filtro ``data`` de
``tarfile`` (sin rutas absolutas ni enlaces fuera de la carpeta).

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
REVISION = "cd92c83884cc7c0ba1a3fda584784f550321e16f"
URL = f"https://huggingface.co/datasets/Francesco/construction-safety-gsnvb/resolve/{REVISION}/dataset.tar.gz"
SHA256 = "70e7b0c3f597f8e6b45e41fb22259de96feba8d763a93305a273384b13842763"
NOMBRE = "gsnvb"
PARTES = ("train", "valid", "test")


def _sha256(archivo: Path) -> str:
    h = hashlib.sha256()
    with archivo.open("rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def descargar(destino: Path = CARPETA_DATOS) -> Path:
    """Descarga y extrae el dataset; devuelve la carpeta con ``train/``, ``valid/`` y ``test/`` (formato COCO)."""
    carpeta = destino / NOMBRE
    if all((carpeta / parte / "_annotations.coco.json").exists() for parte in PARTES):
        print(f"El dataset ya está en {carpeta}")
        return carpeta
    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / "dataset.tar.gz"
    if not archivo.exists() or _sha256(archivo) != SHA256:
        print(f"Descargando {URL} …")
        with urllib.request.urlopen(URL, timeout=60) as respuesta, archivo.open("wb") as salida:
            shutil.copyfileobj(respuesta, salida)
    if _sha256(archivo) != SHA256:
        raise SystemExit(f"El SHA-256 de {archivo} no coincide con el esperado: bórralo y vuelve a descargar.")

    extraido = carpeta / "_extraido"
    shutil.rmtree(extraido, ignore_errors=True)
    with tarfile.open(archivo, "r:gz") as tar:
        tar.extractall(extraido, filter="data")
    # El archivo trae la ruta de quien lo armó (…/rf100/construction-safety-gsnvb/{train,valid,test}).
    anotaciones = sorted(extraido.rglob("_annotations.coco.json"))
    if len(anotaciones) != len(PARTES):
        raise SystemExit(f"Se esperaban {len(PARTES)} archivos _annotations.coco.json y hay {len(anotaciones)}.")
    raiz = anotaciones[0].parent.parent
    for parte in PARTES:
        shutil.rmtree(carpeta / parte, ignore_errors=True)
        shutil.move(str(raiz / parte), str(carpeta / parte))
    for leeme in raiz.glob("README*.txt"):
        shutil.move(str(leeme), str(carpeta / leeme.name))
    shutil.rmtree(extraido)
    print(f"Dataset listo en {carpeta}")
    return carpeta


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--destino", type=Path, default=CARPETA_DATOS)
    args = parser.parse_args()
    descargar(args.destino)


if __name__ == "__main__":
    main()
