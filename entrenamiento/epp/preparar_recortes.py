"""Recortes de personas con sus etiquetas de casco y chaleco (301).

Lee las anotaciones COCO del dataset, recorta cada persona y le asigna dos etiquetas con las reglas del diseño
(«Etiquetado de recortes» de ``docs/specs/301-deteccion-epp/diseno.md``):

- Un recorte por caja de persona de al menos 64 px de alto, con margen de 10 %.
- ``casco = 1`` si una caja de casco tiene ≥ 60 % de su área dentro del 40 % superior de la persona; ``casco = 0`` si
  hay una caja de «sin casco» (o una cabeza sin casco) en esa zona.
- ``chaleco = 1`` si una caja de chaleco tiene ≥ 60 % de su área dentro de la persona; ``chaleco = 0`` si hay una caja
  de «sin chaleco» (o, en datasets anotados por completo, si no hay chaleco).
- Lo que no se puede decidir lleva peso 0 en el entrenamiento; el otro atributo se usa igual.
- División 70 / 15 / 15 por imagen (no por recorte).

Las reglas son funciones puras (sin Pillow) para probarlas con cajas de ejemplo; Pillow solo se usa al recortar.

Uso (desde ``services/ia``)::

    entrenamiento/epp/.venv/Scripts/python -m entrenamiento.epp.preparar_recortes
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

CARPETA_EPP = Path(__file__).resolve().parent

ALTO_MINIMO_PERSONA = 64.0
MARGEN_RECORTE = 0.10
ZONA_CABEZA = 0.40
AREA_MINIMA_DENTRO = 0.60
LADO_RECORTE = 224
PROPORCIONES = (0.70, 0.15, 0.15)
SEMILLA = 2026

ENTRENAMIENTO = "entrenamiento"
VALIDACION = "validacion"
PRUEBA = "prueba"


class Rol(StrEnum):
    PERSONA = "persona"
    CASCO = "casco"
    SIN_CASCO = "sin_casco"
    CABEZA = "cabeza"
    CHALECO = "chaleco"
    SIN_CHALECO = "sin_chaleco"


# Clases del dataset Construction Safety (Roboflow 100, «construction-safety-gsnvb»). La supercategoría
# «construction-safety» no tiene cajas. El dataset no está anotado por completo (no toda persona tiene casco/chaleco
# o su negativo), así que la falta de chaleco no se toma como «sin chaleco».
CLASES_GSNVB: Mapping[str, Rol] = {
    "person": Rol.PERSONA,
    "helmet": Rol.CASCO,
    "no-helmet": Rol.SIN_CASCO,
    "vest": Rol.CHALECO,
    "no-vest": Rol.SIN_CHALECO,
}

# Clases de SHEL5K (Mendeley Data, «Safety Helmet Wearing Dataset»). Las cajas de persona («person_with_helmet»,
# «person_no_helmet» y unas pocas «person») se etiquetan con las mismas reglas geométricas que las demás: casco en la
# zona de la cabeza → con casco; «head» (cabeza sin casco) en esa zona → sin casco. «head_with_helmet» y «face» no se
# usan. SHEL5K no anota chalecos: el chaleco de sus personas queda sin decidir (peso 0).
CLASES_SHEL5K: Mapping[str, Rol] = {
    "person_with_helmet": Rol.PERSONA,
    "person_no_helmet": Rol.PERSONA,
    "person": Rol.PERSONA,
    "helmet": Rol.CASCO,
    "head": Rol.CABEZA,
}

FUENTES = ("gsnvb", "shel5k")


@dataclass(frozen=True)
class Caja:
    """Caja en píxeles de la imagen, como en COCO: esquina superior izquierda, ancho y alto."""

    x: float
    y: float
    ancho: float
    alto: float

    @property
    def area(self) -> float:
        return max(self.ancho, 0.0) * max(self.alto, 0.0)

    def interseccion(self, otra: Caja) -> float:
        ancho = min(self.x + self.ancho, otra.x + otra.ancho) - max(self.x, otra.x)
        alto = min(self.y + self.alto, otra.y + otra.alto) - max(self.y, otra.y)
        return max(ancho, 0.0) * max(alto, 0.0)

    def fraccion_dentro(self, zona: Caja) -> float:
        """Parte del área de esta caja que cae dentro de ``zona`` (0 a 1)."""
        return self.interseccion(zona) / self.area if self.area > 0 else 0.0


@dataclass(frozen=True)
class Objeto:
    rol: Rol
    caja: Caja


@dataclass(frozen=True)
class Etiqueta:
    """Valor 1 (lleva el EPP) o 0 (no lo lleva); peso 0 si no se pudo decidir (el valor entonces no se usa)."""

    valor: int
    peso: int

    @staticmethod
    def de(valor: int | None) -> Etiqueta:
        return Etiqueta(0, 0) if valor is None else Etiqueta(valor, 1)


@dataclass(frozen=True)
class EtiquetasPersona:
    casco: Etiqueta
    chaleco: Etiqueta

    @property
    def tiene_alguna(self) -> bool:
        return self.casco.peso > 0 or self.chaleco.peso > 0


def zona_cabeza(persona: Caja) -> Caja:
    """El 40 % superior de la caja de la persona (todo su ancho)."""
    return Caja(persona.x, persona.y, persona.ancho, persona.alto * ZONA_CABEZA)


def _alguno_dentro(objetos: Iterable[Objeto], rol: Rol, zona: Caja) -> bool:
    return any(o.rol is rol and o.caja.fraccion_dentro(zona) >= AREA_MINIMA_DENTRO for o in objetos)


def etiquetar_casco(persona: Caja, objetos: Sequence[Objeto]) -> int | None:
    """1 con casco, 0 sin casco, ``None`` si no se puede decidir.

    Si en la zona hay a la vez un casco y una caja de «sin casco» (personas superpuestas), no se decide
    (decisión técnica: mejor sin etiqueta que con una etiqueta dudosa). Una cabeza en la zona cuenta como «sin
    casco» solo si no hay casco.
    """
    zona = zona_cabeza(persona)
    con_casco = _alguno_dentro(objetos, Rol.CASCO, zona)
    sin_casco = _alguno_dentro(objetos, Rol.SIN_CASCO, zona)
    if con_casco and sin_casco:
        return None
    if con_casco:
        return 1
    if sin_casco or _alguno_dentro(objetos, Rol.CABEZA, zona):
        return 0
    return None


def etiquetar_chaleco(persona: Caja, objetos: Sequence[Objeto], *, anotado_por_completo: bool = False) -> int | None:
    """1 con chaleco, 0 sin chaleco, ``None`` si no se puede decidir (chaleco y «sin chaleco» a la vez, o ninguno en
    un dataset que no está anotado por completo)."""
    con_chaleco = _alguno_dentro(objetos, Rol.CHALECO, persona)
    sin_chaleco = _alguno_dentro(objetos, Rol.SIN_CHALECO, persona)
    if con_chaleco and sin_chaleco:
        return None
    if con_chaleco:
        return 1
    if sin_chaleco or anotado_por_completo:
        return 0
    return None


def etiquetar_persona(
    persona: Caja, objetos: Sequence[Objeto], *, anotado_por_completo: bool = False
) -> EtiquetasPersona:
    return EtiquetasPersona(
        casco=Etiqueta.de(etiquetar_casco(persona, objetos)),
        chaleco=Etiqueta.de(etiquetar_chaleco(persona, objetos, anotado_por_completo=anotado_por_completo)),
    )


def persona_suficiente(persona: Caja) -> bool:
    """Solo se recortan personas de al menos 64 px de alto (las más chicas no tienen detalle para clasificar)."""
    return persona.alto >= ALTO_MINIMO_PERSONA


def caja_recorte(persona: Caja, ancho_imagen: float, alto_imagen: float, margen: float = MARGEN_RECORTE) -> Caja:
    """Caja de la persona agrandada un 10 % de su ancho a cada lado y un 10 % de su alto arriba y abajo, recortada a
    los bordes de la imagen (la app de obra recorta igual)."""
    x0 = max(persona.x - persona.ancho * margen, 0.0)
    y0 = max(persona.y - persona.alto * margen, 0.0)
    x1 = min(persona.x + persona.ancho + persona.ancho * margen, ancho_imagen)
    y1 = min(persona.y + persona.alto + persona.alto * margen, alto_imagen)
    return Caja(x0, y0, x1 - x0, y1 - y0)


def dividir_por_imagen(
    imagenes: Iterable[str], semilla: int = SEMILLA, proporciones: tuple[float, float, float] = PROPORCIONES
) -> dict[str, str]:
    """Asigna cada imagen (no cada recorte) a entrenamiento, validación o prueba (70 / 15 / 15), siempre igual para la
    misma semilla y el mismo conjunto de imágenes."""
    ids = sorted(set(imagenes))
    random.Random(semilla).shuffle(ids)
    n_entrenamiento = round(len(ids) * proporciones[0])
    n_validacion = round(len(ids) * proporciones[1])
    division: dict[str, str] = {}
    for i, imagen in enumerate(ids):
        if i < n_entrenamiento:
            division[imagen] = ENTRENAMIENTO
        elif i < n_entrenamiento + n_validacion:
            division[imagen] = VALIDACION
        else:
            division[imagen] = PRUEBA
    return division


@dataclass(frozen=True)
class ImagenAnotada:
    fuente: str
    clave: str  # fuente/…/archivo, única entre todas las fuentes (la división es por esta clave)
    ruta: Path  # archivo de la imagen o, si ``miembro`` no es None, el .zip que la contiene
    ancho: int
    alto: int
    objetos: tuple[Objeto, ...]
    miembro: str | None = None


def leer_coco(carpeta: Path, clases: Mapping[str, Rol] = CLASES_GSNVB, fuente: str = "gsnvb") -> list[ImagenAnotada]:
    """Lee ``<parte>/_annotations.coco.json`` de cada parte del dataset y junta todas las imágenes."""
    imagenes: list[ImagenAnotada] = []
    for anotaciones in sorted(carpeta.glob("*/_annotations.coco.json")):
        parte = anotaciones.parent
        datos = json.loads(anotaciones.read_text(encoding="utf-8"))
        roles = {c["id"]: clases[c["name"]] for c in datos["categories"] if c["name"] in clases}
        por_imagen: dict[int, list[Objeto]] = {}
        for a in datos["annotations"]:
            rol = roles.get(a["category_id"])
            if rol is not None:
                x, y, ancho, alto = (float(v) for v in a["bbox"])
                por_imagen.setdefault(a["image_id"], []).append(Objeto(rol, Caja(x, y, ancho, alto)))
        for img in datos["images"]:
            imagenes.append(
                ImagenAnotada(
                    fuente=fuente,
                    clave=f"{fuente}/{parte.name}/{img['file_name']}",
                    ruta=parte / img["file_name"],
                    ancho=int(img["width"]),
                    alto=int(img["height"]),
                    objetos=tuple(por_imagen.get(img["id"], [])),
                )
            )
    return imagenes


def leer_voc_zip(
    archivo: Path, clases: Mapping[str, Rol] = CLASES_SHEL5K, fuente: str = "shel5k"
) -> list[ImagenAnotada]:
    """Lee las anotaciones Pascal VOC (``…/Annotations/*.xml``) de un .zip sin extraerlo; la imagen de cada XML se
    busca en ``…/Images/`` del mismo .zip con el mismo nombre (formato de SHEL5K)."""
    import xml.etree.ElementTree as ET
    import zipfile

    imagenes: list[ImagenAnotada] = []
    with zipfile.ZipFile(archivo) as z:
        nombres = set(z.namelist())
        for xml in sorted(n for n in nombres if n.endswith(".xml") and "/Annotations/" in n):
            raiz = ET.fromstring(z.read(xml))
            carpeta, archivo_xml = xml.rsplit("/Annotations/", 1)
            base = archivo_xml[: -len(".xml")]
            posibles = (f"{carpeta}/Images/{base}.png", f"{carpeta}/Images/{base}.jpg")
            miembro = next((m for m in posibles if m in nombres), None)
            if miembro is None:
                continue
            objetos: list[Objeto] = []
            for o in raiz.iter("object"):
                rol = clases.get(o.findtext("name", ""))
                caja = o.find("bndbox")
                if rol is None or caja is None:
                    continue
                x0, y0, x1, y1 = (float(caja.findtext(k, "0")) for k in ("xmin", "ymin", "xmax", "ymax"))
                objetos.append(Objeto(rol, Caja(x0, y0, x1 - x0, y1 - y0)))
            imagenes.append(
                ImagenAnotada(
                    fuente=fuente,
                    clave=f"{fuente}/{base}",
                    ruta=archivo,
                    ancho=int(raiz.findtext("size/width", "0")),
                    alto=int(raiz.findtext("size/height", "0")),
                    objetos=tuple(objetos),
                    miembro=miembro,
                )
            )
    return imagenes


COLUMNAS = ("archivo", "fuente", "imagen", "division", "casco", "peso_casco", "chaleco", "peso_chaleco")


def leer_fuentes(fuentes: Sequence[str], datos: Path) -> list[ImagenAnotada]:
    imagenes: list[ImagenAnotada] = []
    for fuente in fuentes:
        if fuente == "gsnvb":
            imagenes += leer_coco(datos / "gsnvb", CLASES_GSNVB, fuente)
        elif fuente == "shel5k":
            imagenes += leer_voc_zip(datos / "shel5k" / "shel5k.zip", CLASES_SHEL5K, fuente)
        else:
            raise SystemExit(f"Fuente desconocida: {fuente}")
    return imagenes


def preparar(
    datos: Path,
    salida: Path,
    *,
    fuentes: Sequence[str] = FUENTES,
    semilla: int = SEMILLA,
    anotado_por_completo: bool = False,
) -> dict[str, object]:
    """Recorta las personas de todas las imágenes, guarda los recortes de 224 × 224 y ``recortes.csv``; devuelve el
    resumen (también en ``resumen.json``)."""
    import io
    import zipfile

    from PIL import Image  # solo aquí: las reglas se prueban sin Pillow

    imagenes = leer_fuentes(fuentes, datos)
    if not imagenes:
        raise SystemExit(f"No hay anotaciones en {datos}: corre antes descargar_dataset.")
    division = dividir_por_imagen((i.clave for i in imagenes), semilla)
    salida.mkdir(parents=True, exist_ok=True)
    filas: list[dict[str, object]] = []
    descartes: Counter[str] = Counter()
    zips: dict[Path, zipfile.ZipFile] = {}
    try:
        for imagen in imagenes:
            personas = [o.caja for o in imagen.objetos if o.rol is Rol.PERSONA]
            if not personas:
                continue
            if imagen.miembro is None:
                with Image.open(imagen.ruta) as original:
                    rgb = original.convert("RGB")
            else:
                z = zips.setdefault(imagen.ruta, zipfile.ZipFile(imagen.ruta))
                with Image.open(io.BytesIO(z.read(imagen.miembro))) as original:
                    rgb = original.convert("RGB")
            for n, persona in enumerate(personas):
                if not persona_suficiente(persona):
                    descartes["persona_chica"] += 1
                    continue
                etiquetas = etiquetar_persona(persona, imagen.objetos, anotado_por_completo=anotado_por_completo)
                if not etiquetas.tiene_alguna:
                    descartes["sin_etiquetas"] += 1
                    continue
                caja = caja_recorte(persona, rgb.width, rgb.height)
                limites = (round(caja.x), round(caja.y), round(caja.x + caja.ancho), round(caja.y + caja.alto))
                recorte = rgb.crop(limites).resize((LADO_RECORTE, LADO_RECORTE), Image.Resampling.BILINEAR)
                parte = division[imagen.clave]
                nombre = f"{parte}/{imagen.clave.replace('/', '_').rsplit('.', 1)[0]}_{n:02d}.jpg"
                (salida / parte).mkdir(exist_ok=True)
                recorte.save(salida / nombre, quality=95)
                filas.append(
                    {
                        "archivo": nombre,
                        "fuente": imagen.fuente,
                        "imagen": imagen.clave,
                        "division": parte,
                        "casco": etiquetas.casco.valor,
                        "peso_casco": etiquetas.casco.peso,
                        "chaleco": etiquetas.chaleco.valor,
                        "peso_chaleco": etiquetas.chaleco.peso,
                    }
                )
    finally:
        for z in zips.values():
            z.close()
    with (salida / "recortes.csv").open("w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS)
        escritor.writeheader()
        escritor.writerows(filas)
    resumen = {
        "semilla": semilla,
        "fuentes": list(fuentes),
        "imagenes": len(imagenes),
        "recortes": len(filas),
        "descartes": dict(descartes),
        "por_division": cuentas(filas, division),
        "por_fuente": {
            fuente: cuentas(
                [f for f in filas if f["fuente"] == fuente],
                {k: v for k, v in division.items() if k.startswith(f"{fuente}/")},
            )
            for fuente in fuentes
        },
    }
    (salida / "resumen.json").write_text(json.dumps(resumen, indent=2, ensure_ascii=False), encoding="utf-8")
    return resumen


def cuentas(filas: Sequence[Mapping[str, object]], division: Mapping[str, str]) -> dict[str, dict[str, int]]:
    """Imágenes, recortes y etiquetas por división."""
    por_division: dict[str, dict[str, int]] = {}
    for parte in (ENTRENAMIENTO, VALIDACION, PRUEBA):
        de_parte = [f for f in filas if f["division"] == parte]
        por_division[parte] = {
            "imagenes": sum(1 for v in division.values() if v == parte),
            "recortes": len(de_parte),
            "con_casco": sum(1 for f in de_parte if f["peso_casco"] == 1 and f["casco"] == 1),
            "sin_casco": sum(1 for f in de_parte if f["peso_casco"] == 1 and f["casco"] == 0),
            "casco_indeterminado": sum(1 for f in de_parte if f["peso_casco"] == 0),
            "con_chaleco": sum(1 for f in de_parte if f["peso_chaleco"] == 1 and f["chaleco"] == 1),
            "sin_chaleco": sum(1 for f in de_parte if f["peso_chaleco"] == 1 and f["chaleco"] == 0),
            "chaleco_indeterminado": sum(1 for f in de_parte if f["peso_chaleco"] == 0),
        }
    return por_division


def main() -> None:
    parser = argparse.ArgumentParser(description="Recortes de personas con etiquetas de casco y chaleco (301).")
    parser.add_argument("--datos", type=Path, default=CARPETA_EPP / "datos")
    parser.add_argument("--salida", type=Path, default=CARPETA_EPP / "datos" / "recortes")
    parser.add_argument("--fuentes", nargs="+", default=list(FUENTES), choices=list(FUENTES))
    parser.add_argument("--semilla", type=int, default=SEMILLA)
    args = parser.parse_args()
    resumen = preparar(args.datos, args.salida, fuentes=args.fuentes, semilla=args.semilla)
    print(json.dumps(resumen, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
