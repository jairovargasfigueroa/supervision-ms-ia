"""Cruce del paso compuesto `consumo_vs_previsto` (505): funciones puras, sin base ni servidores."""

from typing import Any

from ia.agentes.dominio.consumo_previsto import FILAS_MAXIMAS, cruzar

O1 = "B0000000-0000-4000-8000-000000000001"


def _avance(*materiales: tuple[str, Any], partida: str = "P1") -> dict[str, Any]:
    previsto = [{"materialId": m, "cantidadPrevista": c} for m, c in materiales]
    return {"obras": [{"obraId": O1, "partidas": [{"partidaId": partida, "materialPrevisto": previsto}]}]}


def _fila(material: str, usado: Any, sin_stock: Any = 0, partida: str = "p1") -> dict[str, Any]:
    return {"obraId": O1.lower(), "obraNombre": "Las Palmas", "partidaId": partida, "materialId": material,
            "nombre": f"Material {material}", "usado": usado, "salidasSuperanStock": sin_stock}  # fmt: skip


def test_exceso_solo_por_encima_del_20_por_ciento_y_de_mayor_a_menor() -> None:
    avance = _avance(("m1", 100), ("m2", "10.5"), ("m3", 50), ("m4", 0))
    consumos = {"filas": [_fila("m1", 120), _fila("m2", 21), _fila("m3", 61), _fila("m4", 5), _fila("m5", 99)]}
    cruce = cruzar(avance, consumos, "2027-04")
    # m1: 120 de 100 es exactamente 1,20 × previsto (no excede); m4 sin previsto (0) y m5 sin material previsto
    assert [(e["materialId"], e["porcentaje"]) for e in cruce["excesos"]] == [("m2", 200.0), ("m3", 122.0)]
    assert cruce["excesos"][0] == {
        "obraId": O1.lower(),
        "obraNombre": "Las Palmas",
        "partidaId": "p1",
        "materialId": "m2",
        "material": "Material m2",
        "previsto": 10.5,
        "usado": 21.0,
        "porcentaje": 200.0,
    }
    assert cruce["salidasSinStock"] == []
    assert (cruce["periodo"], cruce["truncado"]) == ("2027-04", False)


def test_salidas_sin_stock_de_mas_a_menos_y_sin_valores_raros() -> None:
    consumos = {"filas": [_fila("m1", 1, 1), _fila("m2", 1, 3), _fila("m3", 1, True), _fila("m4", "x", "2")]}
    cruce = cruzar(_avance(), consumos, "2027-04")
    assert [(s["material"], s["salidasSuperanStock"]) for s in cruce["salidasSinStock"]] == [
        ("Material m2", 3),
        ("Material m1", 1),
    ]
    assert set(cruce["salidasSinStock"][0]) == {"obraId", "obraNombre", "partidaId", "material", "salidasSuperanStock"}


def test_sin_avance_no_hay_excesos_y_se_recorta_a_30_filas() -> None:
    consumos = {"filas": [_fila(f"m{i}", 1, i + 1) for i in range(FILAS_MAXIMAS + 5)], "truncado": False}
    cruce = cruzar(None, consumos, "2027-04")
    assert "excesos" not in cruce
    assert len(cruce["salidasSinStock"]) == FILAS_MAXIMAS
    assert cruce["salidasSinStock"][0]["salidasSuperanStock"] == FILAS_MAXIMAS + 5
    assert cruce["truncado"] is True


def test_truncado_si_alguna_herramienta_recorto_y_datos_mal_formados_no_rompen() -> None:
    assert cruzar({"obras": [], "truncado": True}, {"filas": []}, "2027-04")["truncado"] is True
    assert cruzar({"obras": "x"}, {"filas": [1, None, {"usado": 5}]}, "2027-04") == {
        "periodo": "2027-04",
        "excesos": [],
        "salidasSinStock": [],
        "truncado": False,
    }
