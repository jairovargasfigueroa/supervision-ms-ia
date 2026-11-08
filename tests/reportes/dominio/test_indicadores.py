from datetime import date, timedelta
from decimal import Decimal

from ia.reportes.dominio.indicadores import (
    avance_pct,
    avance_periodo_pct,
    estado_cobro,
    monto_consumo,
    monto_periodo,
    plazo_pct,
    redondear,
)

D = Decimal


def test_avance_con_varias_planillas_aprobadas_datos_011() -> None:
    # Obra 1: (349.560,00 + 440.680,00) / 2.773.800,00 = 28,49 %; obra 2: 520.340,00 / 3.185.140,00 = 16,34 %.
    assert avance_pct([D("349560.00"), D("440680.00")], D("2773800.00")) == D("28.49")
    assert avance_pct([D("520340.00")], D("3185140.00")) == D("16.34")
    assert avance_pct([], D("1900140.00")) == D("0.00")


def test_avance_redondea_mitad_hacia_arriba() -> None:
    # 1 / 800 × 100 = 0,125 → 0,13 (mitad hacia arriba).
    assert avance_pct([D("1.00")], D("800.00")) == D("0.13")
    assert avance_pct([D("1.00")], D("1600.00")) == D("0.06")  # 0,0625 → 0,06
    assert redondear(D("2.345")) == D("2.35")
    assert redondear(D("2.344")) == D("2.34")


def test_avance_con_monto_cero_es_cero() -> None:
    assert avance_pct([D("10")], D("0")) == D("0.00")


def test_monto_y_avance_del_periodo() -> None:
    monto = monto_periodo([D("55800.00"), D("293760.00"), D("0")])
    assert monto == D("349560.00")
    assert avance_periodo_pct(monto, D("2773800.00")) == D("12.60")


def test_plazo_antes_de_iniciar_es_cero() -> None:
    inicio = date(2026, 11, 1)
    assert plazo_pct(inicio, 300, date(2026, 10, 20)) == D("0.00")
    assert plazo_pct(inicio, 300, inicio) == D("0.00")


def test_plazo_a_mitad() -> None:
    inicio = date(2026, 6, 30)
    assert plazo_pct(inicio, 360, inicio + timedelta(days=120)) == D("33.33")
    assert plazo_pct(inicio, 300, inicio + timedelta(days=150)) == D("50.00")
    assert plazo_pct(inicio, 240, inicio + timedelta(days=5)) == D("2.08")


def test_plazo_vencido_es_cien() -> None:
    inicio = date(2026, 1, 1)
    assert plazo_pct(inicio, 100, inicio + timedelta(days=100)) == D("100.00")
    assert plazo_pct(inicio, 100, inicio + timedelta(days=500)) == D("100.00")


def test_cobro_vence_hoy_sigue_pendiente_y_vencio_ayer_es_vencido() -> None:
    hoy = date(2026, 10, 31)
    assert estado_cobro(hoy, hoy) == "PENDIENTE"
    assert estado_cobro(hoy - timedelta(days=1), hoy) == "VENCIDO"
    assert estado_cobro(hoy + timedelta(days=10), hoy) == "PENDIENTE"
    assert estado_cobro(None, hoy) == "PENDIENTE"


def test_monto_de_consumo() -> None:
    assert monto_consumo(D("12.5"), D("62.00")) == D("775.00")
    assert monto_consumo(D("0.333"), D("8.60")) == D("2.86")
    assert monto_consumo(D("12.5"), None) == D("0.00")
