"""Reglas de etiquetado de los recortes de personas (301) con cajas de ejemplo."""

import json
from pathlib import Path

from entrenamiento.epp.preparar_recortes import (
    ENTRENAMIENTO,
    PRUEBA,
    VALIDACION,
    Caja,
    Etiqueta,
    Objeto,
    Rol,
    caja_recorte,
    dividir_por_imagen,
    etiquetar_casco,
    etiquetar_chaleco,
    etiquetar_persona,
    leer_coco,
    persona_suficiente,
    zona_cabeza,
)

# Persona de 100 × 200 px en (100, 100): la zona de la cabeza es el 40 % superior, de y = 100 a y = 180.
PERSONA = Caja(100, 100, 100, 200)


def _objeto(rol: Rol, x: float, y: float, ancho: float, alto: float) -> Objeto:
    return Objeto(rol, Caja(x, y, ancho, alto))


def test_zona_de_la_cabeza_es_el_40_por_ciento_superior() -> None:
    assert zona_cabeza(PERSONA) == Caja(100, 100, 100, 80)


def test_fraccion_dentro_de_una_zona() -> None:
    caja = Caja(0, 0, 10, 10)
    assert caja.fraccion_dentro(Caja(0, 0, 10, 10)) == 1.0
    assert caja.fraccion_dentro(Caja(5, 0, 10, 10)) == 0.5
    assert caja.fraccion_dentro(Caja(20, 20, 5, 5)) == 0.0
    assert Caja(0, 0, 0, 10).fraccion_dentro(Caja(0, 0, 10, 10)) == 0.0


def test_casco_dentro_de_la_zona_de_la_cabeza_es_con_casco() -> None:
    casco = _objeto(Rol.CASCO, 130, 100, 40, 30)
    assert etiquetar_casco(PERSONA, [casco]) == 1


def test_casco_con_60_por_ciento_de_su_area_en_la_zona_cuenta() -> None:
    # 40 × 50 px desde y = 150: 30 de 50 px de alto dentro de la zona (hasta y = 180) → 60 %.
    assert etiquetar_casco(PERSONA, [_objeto(Rol.CASCO, 130, 150, 40, 50)]) == 1
    # Desde y = 151: 29 de 50 → 58 %, no cuenta.
    assert etiquetar_casco(PERSONA, [_objeto(Rol.CASCO, 130, 151, 40, 50)]) is None


def test_casco_por_debajo_de_la_zona_no_cuenta() -> None:
    # Un casco en la mano, a la altura de la cintura.
    assert etiquetar_casco(PERSONA, [_objeto(Rol.CASCO, 120, 220, 40, 30)]) is None


def test_casco_de_otra_persona_no_cuenta() -> None:
    assert etiquetar_casco(PERSONA, [_objeto(Rol.CASCO, 300, 100, 40, 30)]) is None


def test_sin_casco_en_la_zona_es_sin_casco() -> None:
    assert etiquetar_casco(PERSONA, [_objeto(Rol.SIN_CASCO, 130, 100, 40, 40)]) == 0


def test_cabeza_sin_casco_es_sin_casco_y_con_casco_es_con_casco() -> None:
    cabeza = _objeto(Rol.CABEZA, 130, 105, 40, 40)
    assert etiquetar_casco(PERSONA, [cabeza]) == 0
    assert etiquetar_casco(PERSONA, [cabeza, _objeto(Rol.CASCO, 128, 100, 44, 25)]) == 1


def test_casco_y_sin_casco_a_la_vez_no_se_decide() -> None:
    objetos = [_objeto(Rol.CASCO, 110, 100, 30, 30), _objeto(Rol.SIN_CASCO, 160, 100, 30, 40)]
    assert etiquetar_casco(PERSONA, objetos) is None


def test_sin_cajas_de_casco_no_se_decide() -> None:
    assert etiquetar_casco(PERSONA, []) is None


def test_chaleco_dentro_de_la_persona_es_con_chaleco() -> None:
    assert etiquetar_chaleco(PERSONA, [_objeto(Rol.CHALECO, 110, 170, 80, 80)]) == 1


def test_chaleco_con_menos_del_60_por_ciento_dentro_no_cuenta() -> None:
    # 80 px de ancho desde x = 160: 40 de 80 dentro de la persona (hasta x = 200) → 50 %.
    assert etiquetar_chaleco(PERSONA, [_objeto(Rol.CHALECO, 160, 170, 80, 80)]) is None


def test_sin_chaleco_es_sin_chaleco() -> None:
    assert etiquetar_chaleco(PERSONA, [_objeto(Rol.SIN_CHALECO, 110, 170, 80, 80)]) == 0


def test_chaleco_y_sin_chaleco_a_la_vez_no_se_decide() -> None:
    objetos = [_objeto(Rol.CHALECO, 110, 170, 80, 80), _objeto(Rol.SIN_CHALECO, 105, 165, 90, 90)]
    assert etiquetar_chaleco(PERSONA, objetos) is None


def test_sin_cajas_de_chaleco_depende_de_si_el_dataset_esta_anotado_por_completo() -> None:
    assert etiquetar_chaleco(PERSONA, []) is None
    assert etiquetar_chaleco(PERSONA, [], anotado_por_completo=True) == 0


def test_lo_indeterminado_lleva_peso_0_y_el_otro_atributo_se_usa_igual() -> None:
    etiquetas = etiquetar_persona(PERSONA, [_objeto(Rol.CHALECO, 110, 170, 80, 80)])
    assert etiquetas.casco == Etiqueta(0, 0)
    assert etiquetas.chaleco == Etiqueta(1, 1)
    assert etiquetas.tiene_alguna
    assert not etiquetar_persona(PERSONA, []).tiene_alguna


def test_persona_de_menos_de_64_px_de_alto_no_se_recorta() -> None:
    assert persona_suficiente(Caja(0, 0, 30, 64))
    assert not persona_suficiente(Caja(0, 0, 30, 63.9))


def test_recorte_con_margen_de_10_por_ciento_y_dentro_de_la_imagen() -> None:
    assert caja_recorte(PERSONA, 640, 640) == Caja(90, 80, 120, 240)
    # Junto al borde, el margen se corta en la imagen.
    assert caja_recorte(Caja(0, 0, 100, 200), 150, 210) == Caja(0, 0, 110, 210)


def test_division_70_15_15_por_imagen_y_reproducible() -> None:
    imagenes = [f"img_{i:03d}.jpg" for i in range(200)]
    division = dividir_por_imagen(imagenes, semilla=2026)
    cuentas = {parte: sum(1 for v in division.values() if v == parte) for parte in (ENTRENAMIENTO, VALIDACION, PRUEBA)}
    assert cuentas == {ENTRENAMIENTO: 140, VALIDACION: 30, PRUEBA: 30}
    # Repetidas, el orden no importa y la misma semilla da la misma división.
    assert dividir_por_imagen([*reversed(imagenes), imagenes[0]], semilla=2026) == division
    assert dividir_por_imagen(imagenes, semilla=7) != division


def test_leer_coco_junta_las_partes_y_traduce_las_clases(tmp_path: Path) -> None:
    for parte, nombre in (("train", "a.jpg"), ("test", "a.jpg")):
        (tmp_path / parte).mkdir()
        coco = {
            "categories": [
                {"id": 0, "name": "construction-safety"},
                {"id": 1, "name": "helmet"},
                {"id": 4, "name": "person"},
            ],
            "images": [{"id": 0, "file_name": nombre, "width": 640, "height": 480}],
            "annotations": [
                {"id": 0, "image_id": 0, "category_id": 4, "bbox": [10, 20, 30, 100]},
                {"id": 1, "image_id": 0, "category_id": 1, "bbox": [15, 20, 20, 15]},
            ],
        }
        (tmp_path / parte / "_annotations.coco.json").write_text(json.dumps(coco), encoding="utf-8")

    imagenes = leer_coco(tmp_path)

    assert sorted(i.clave for i in imagenes) == ["test/a.jpg", "train/a.jpg"]
    assert imagenes[0].objetos == (
        Objeto(Rol.PERSONA, Caja(10, 20, 30, 100)),
        Objeto(Rol.CASCO, Caja(15, 20, 20, 15)),
    )
    assert (imagenes[0].ancho, imagenes[0].alto) == (640, 480)
