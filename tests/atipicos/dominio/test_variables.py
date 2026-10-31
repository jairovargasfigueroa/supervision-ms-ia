"""`calcular_variables_salida` con casos fijos (403, diseño «Variables» y «Pruebas»)."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from ia.atipicos.dominio.historial import HistorialEnMemoria, recorrer_en_orden
from ia.atipicos.dominio.variables import (
    NOMBRES_VARIABLES,
    Historial,
    SalidaMaterial,
    SalidaPrevia,
    calcular_variables_salida,
)

# 2026-11-11 es miércoles; La Paz = UTC−4.
MIERCOLES_10_LOCAL = datetime(2026, 11, 11, 14, 0, tzinfo=UTC)


def salida(
    cantidad: float = 10.0,
    registrado_en: datetime = MIERCOLES_10_LOCAL,
    *,
    periodo: str = "2026-11",
    supera_stock: bool = False,
    obra: str = "o1",
    material: str = "m1",
    id_: str = "s",
) -> SalidaMaterial:
    return SalidaMaterial(
        movimiento_id=id_,
        obra_id=obra,
        material_id=material,
        cantidad=cantidad,
        periodo=periodo,
        supera_stock=supera_stock,
        registrado_en=registrado_en,
    )


def local(dia: int, hora: int, minuto: int = 0) -> datetime:
    """Instante UTC de una hora local de La Paz en noviembre de 2026."""
    return datetime(2026, 11, dia, hora, minuto, tzinfo=UTC) + timedelta(hours=4)


def previas(cantidades: list[float], ultima: datetime, cada_horas: float = 24) -> list[SalidaPrevia]:
    n = len(cantidades)
    return [
        SalidaPrevia(cantidad, ultima - timedelta(hours=cada_horas * (n - 1 - i)))
        for i, cantidad in enumerate(cantidades)
    ]


def test_historial_vacio_usa_los_valores_neutros() -> None:
    v = calcular_variables_salida(salida(10), Historial())
    assert v.log_cantidad == pytest.approx(math.log(11))
    assert v.z_obra_material == 0
    assert v.razon_material == 1
    assert v.razon_periodo == 1
    assert v.log_horas_anterior == pytest.approx(math.log1p(720))
    assert v.minutos_anterior is None
    assert v.salidas_dia == 1
    assert v.veces_obra_material is None
    assert (v.fuera_horario, v.domingo, v.supera_stock) == (0, 0, 0)
    assert v.hora_local == "10:00"
    assert v.hora_sin == pytest.approx(math.sin(2 * math.pi * 10 / 24))
    assert v.hora_cos == pytest.approx(math.cos(2 * math.pi * 10 / 24))
    assert len(v.vector()) == len(NOMBRES_VARIABLES)
    assert list(v.como_dict()) == list(NOMBRES_VARIABLES)


def test_historial_corto_de_la_obra_usa_el_material_en_todas_las_obras() -> None:
    # 3 salidas en la obra (< 5): z contra las 6 del material en todas las obras.
    historial = Historial(
        obra_material=previas([10, 12, 11], local(10, 9)),
        material=[8, 10, 12, 10, 9, 11],
    )
    v = calcular_variables_salida(salida(20), historial)
    # mediana 10, MAD = mediana(|x − 10|) = mediana(2, 0, 2, 0, 1, 1) = 1 → z = 10 / 1,4826
    assert v.z_obra_material == pytest.approx(10 / 1.4826)
    assert v.razon_material == pytest.approx(2.0)
    assert v.veces_obra_material is None


def test_historial_corto_sin_material_suficiente_da_z_cero() -> None:
    historial = Historial(obra_material=previas([10, 12], local(10, 9)), material=[10, 12])
    v = calcular_variables_salida(salida(100), historial)
    assert v.z_obra_material == 0
    assert v.razon_material == pytest.approx(100 / 11)


def test_historial_largo_usa_las_ultimas_50_de_la_obra_y_recorta_z() -> None:
    # 60 salidas: las 10 primeras (1000) quedan fuera de la ventana; las 50 últimas alternan 9 y 11.
    cantidades = [1000.0] * 10 + [9.0, 11.0] * 25
    historial = Historial(obra_material=previas(cantidades, local(10, 9)), material=cantidades)
    v = calcular_variables_salida(salida(12), historial)
    # mediana 10, MAD 1 → z = 2 / 1,4826
    assert v.z_obra_material == pytest.approx(2 / 1.4826)
    assert v.veces_obra_material == pytest.approx(1.2)
    muy_grande = calcular_variables_salida(salida(500), historial)
    assert muy_grande.z_obra_material == 10
    muy_chica = calcular_variables_salida(salida(0), historial)
    assert muy_chica.z_obra_material == pytest.approx(-10 / 1.4826)


def test_mad_cero_da_cero_si_es_igual_y_diez_si_no() -> None:
    historial = Historial(obra_material=previas([10] * 6, local(10, 9)), material=[10] * 6)
    assert calcular_variables_salida(salida(10), historial).z_obra_material == 0
    assert calcular_variables_salida(salida(11), historial).z_obra_material == 10
    assert calcular_variables_salida(salida(9), historial).z_obra_material == -10


def test_razon_material_usa_las_ultimas_200() -> None:
    material = [1000.0] * 50 + [10.0] * 200
    v = calcular_variables_salida(salida(30), Historial(material=material))
    assert v.razon_material == pytest.approx(3.0)


def test_razon_periodo_incluye_la_salida_y_se_limita_a_diez() -> None:
    historial = Historial(consumo_por_periodo={"2026-09": 100.0, "2026-10": 300.0, "2026-11": 150.0})
    v = calcular_variables_salida(salida(50), historial)
    assert v.razon_periodo == pytest.approx(200 / 200)
    v = calcular_variables_salida(salida(1850), historial)
    assert v.razon_periodo == 10
    # Solo el periodo actual: sin periodos anteriores, 1.
    assert calcular_variables_salida(salida(50), Historial(consumo_por_periodo={"2026-11": 10.0})).razon_periodo == 1


@pytest.mark.parametrize(
    ("hora", "minuto", "fuera"),
    [(5, 59, 1), (6, 0, 0), (19, 59, 0), (20, 0, 1), (22, 40, 1), (0, 0, 1)],
)
def test_borde_del_horario_habitual(hora: int, minuto: int, fuera: int) -> None:
    v = calcular_variables_salida(salida(registrado_en=local(11, hora, minuto)), Historial())
    assert v.fuera_horario == fuera
    assert v.hora_local == f"{hora:02d}:{minuto:02d}"


def test_domingo_en_hora_local() -> None:
    # Domingo 15/11 a las 23:30 en La Paz es lunes 16/11 03:30 en UTC: cuenta como domingo.
    domingo_noche = calcular_variables_salida(salida(registrado_en=local(15, 23, 30)), Historial())
    assert domingo_noche.domingo == 1
    sabado = calcular_variables_salida(salida(registrado_en=local(14, 10)), Historial())
    assert sabado.domingo == 0
    lunes = calcular_variables_salida(salida(registrado_en=local(16, 0, 30)), Historial())
    assert lunes.domingo == 0


def test_supera_stock() -> None:
    assert calcular_variables_salida(salida(supera_stock=True), Historial()).supera_stock == 1


def test_horas_desde_la_anterior_y_salidas_del_dia() -> None:
    ahora = local(11, 10)
    historial = Historial(
        obra_material=[
            SalidaPrevia(10, local(10, 18)),  # otro día
            SalidaPrevia(10, local(11, 7)),
            SalidaPrevia(10, local(11, 9, 30)),
        ]
    )
    v = calcular_variables_salida(salida(registrado_en=ahora), historial)
    assert v.minutos_anterior == pytest.approx(30)
    assert v.log_horas_anterior == pytest.approx(math.log1p(0.5))
    assert v.salidas_dia == 3
    # Más de 720 h: se limita.
    lejos = Historial(obra_material=[SalidaPrevia(10, ahora - timedelta(days=60))])
    assert calcular_variables_salida(salida(registrado_en=ahora), lejos).log_horas_anterior == pytest.approx(
        math.log1p(720)
    )


def test_historial_en_memoria_da_lo_mismo_que_armarlo_a_mano() -> None:
    salidas = [salida(10 + i, local(1 + i, 9), id_=f"s{i}") for i in range(8)]
    memoria = HistorialEnMemoria()
    for anterior in salidas[:7]:
        memoria.agregar(anterior)
    a_mano = Historial(
        obra_material=[SalidaPrevia(s.cantidad, s.registrado_en) for s in salidas[:7]],
        material=[s.cantidad for s in salidas[:7]],
        consumo_por_periodo={"2026-11": sum(s.cantidad for s in salidas[:7])},
    )
    assert calcular_variables_salida(salidas[7], memoria.historial_de(salidas[7])) == calcular_variables_salida(
        salidas[7], a_mano
    )


def test_recorrer_en_orden_no_compara_salidas_del_mismo_instante() -> None:
    mismo = local(11, 10)
    resultado = list(
        recorrer_en_orden([salida(5, local(11, 11), id_="c"), salida(5, mismo, id_="b"), salida(5, mismo, id_="a")])
    )
    assert [s.movimiento_id for s, _ in resultado] == ["a", "b", "c"]
    assert [v.salidas_dia for _, v in resultado] == [1, 1, 3]
