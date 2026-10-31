"""Motivos con percentiles fijos (403, diseño «Motivos»)."""

from dataclasses import replace

import pytest

from ia.atipicos.dominio.motivos import MOTIVO_GENERICO, Percentiles, motivos_consumo
from ia.atipicos.dominio.variables import NOMBRES_VARIABLES, VariablesSalida

P99 = {
    "log_cantidad": 6.0,
    "z_obra_material": 3.0,
    "razon_material": 3.5,
    "razon_periodo": 2.5,
    "hora_sin": 1.0,
    "hora_cos": 1.0,
    "fuera_horario": 0.0,
    "domingo": 0.0,
    "supera_stock": 0.0,
    "log_horas_anterior": 6.58,
    "salidas_dia": 3.0,
}
P1 = {**{nombre: 0.0 for nombre in NOMBRES_VARIABLES}, "log_horas_anterior": 0.7}
PERCENTILES = Percentiles(p1=P1, p99=P99)

NORMAL = VariablesSalida(
    log_cantidad=2.4,
    z_obra_material=0.3,
    razon_material=1.1,
    razon_periodo=0.8,
    hora_sin=0.5,
    hora_cos=-0.86,
    fuera_horario=0,
    domingo=0,
    supera_stock=0,
    log_horas_anterior=3.2,
    salidas_dia=1,
    veces_obra_material=1.05,
    hora_local="10:00",
    minutos_anterior=1440.0,
)


def test_sin_reglas_que_se_cumplan_da_el_motivo_generico() -> None:
    assert motivos_consumo(NORMAL, PERCENTILES) == [MOTIVO_GENERICO]


def test_cantidad_y_horario_del_ejemplo_de_la_spec() -> None:
    v = replace(NORMAL, z_obra_material=10.0, veces_obra_material=4.2, fuera_horario=1, hora_local="22:40")
    assert motivos_consumo(v, PERCENTILES) == [
        "La cantidad es 4,2 veces la habitual de este material en la obra",
        "Se registró fuera del horario habitual (22:40)",
    ]


def test_cada_regla_tiene_su_texto() -> None:
    casos = [
        (
            {"razon_material": 7.0},
            "La cantidad es 7,0 veces la habitual de este material en todas las obras",
        ),
        ({"razon_periodo": 3.25}, "El consumo del mes ya es 3,3 veces el promedio mensual"),
        ({"domingo": 1}, "Se registró en domingo"),
        ({"supera_stock": 1}, "Salió más material del que había en stock"),
        ({"salidas_dia": 5}, "Hubo 5 salidas de este material el mismo día"),
        ({"log_horas_anterior": 0.2, "minutos_anterior": 13.4}, "Salida muy seguida de la anterior (13 minutos)"),
        ({"log_horas_anterior": 0.01, "minutos_anterior": 1.0}, "Salida muy seguida de la anterior (1 minuto)"),
    ]
    for cambios, texto in casos:
        assert motivos_consumo(replace(NORMAL, **cambios), PERCENTILES) == [texto], cambios


def test_hasta_tres_del_mas_fuerte_al_mas_debil() -> None:
    v = replace(
        NORMAL,
        z_obra_material=4.5,  # 1 + 1,5/3 = 1,5
        veces_obra_material=2.6,
        razon_material=14.0,  # 1 + 10,5/3,5 = 4
        domingo=1,  # 1
        supera_stock=1,  # 1
        salidas_dia=6,  # 1 + 3/3 = 2
    )
    assert motivos_consumo(v, PERCENTILES) == [
        "La cantidad es 14,0 veces la habitual de este material en todas las obras",
        "Hubo 6 salidas de este material el mismo día",
        "La cantidad es 2,6 veces la habitual de este material en la obra",
    ]


def test_a_igual_fuerza_manda_el_orden_de_la_tabla() -> None:
    v = replace(NORMAL, supera_stock=1, domingo=1, fuera_horario=1, hora_local="05:10")
    assert motivos_consumo(v, PERCENTILES) == [
        "Se registró fuera del horario habitual (05:10)",
        "Se registró en domingo",
        "Salió más material del que había en stock",
    ]


def test_reglas_que_no_tendrian_sentido_no_se_muestran() -> None:
    # z alto contra el material en todas las obras (sin historial de la obra): no se dice «en la obra».
    assert motivos_consumo(replace(NORMAL, z_obra_material=8.0, veces_obra_material=None), PERCENTILES) == [
        MOTIVO_GENERICO
    ]
    # Percentil 99 de salidas del día = 1: una sola salida no es «muchas».
    bajos = Percentiles(p1=P1, p99={**P99, "salidas_dia": 1.0})
    assert motivos_consumo(NORMAL, bajos) == [MOTIVO_GENERICO]
    # Sin salida anterior no hay «muy seguida».
    assert motivos_consumo(replace(NORMAL, log_horas_anterior=0.0, minutos_anterior=None), PERCENTILES) == [
        MOTIVO_GENERICO
    ]


def test_percentiles_ida_y_vuelta_y_faltantes() -> None:
    assert Percentiles.desde_dict(PERCENTILES.como_dict()) == PERCENTILES
    incompleto = PERCENTILES.como_dict()
    del incompleto["domingo"]
    with pytest.raises(ValueError, match="domingo"):
        Percentiles.desde_dict(incompleto)
