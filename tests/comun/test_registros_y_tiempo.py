import json
import logging
from datetime import UTC, date, datetime

import pytest

from ia.comun.registros import FormatoJson, FormatoTexto, _FiltroContexto, id_correlacion, id_peticion
from ia.comun.tiempo import RelojFijo, fecha_en_zona, iso_utc, leer_instante


def _registro(mensaje: str) -> logging.LogRecord:
    registro = logging.LogRecord("ia.prueba", logging.INFO, __file__, 1, mensaje, None, None)
    _FiltroContexto().filter(registro)
    return registro


def test_json_incluye_request_id_y_correlacion() -> None:
    marca_p = id_peticion.set("req-1")
    marca_c = id_correlacion.set("corr-1")
    try:
        linea = json.loads(FormatoJson().format(_registro("hola")))
    finally:
        id_peticion.reset(marca_p)
        id_correlacion.reset(marca_c)
    assert linea["mensaje"] == "hola"
    assert linea["nivel"] == "INFO"
    assert linea["requestId"] == "req-1"
    assert linea["correlacionId"] == "corr-1"
    assert linea["ts"].endswith("Z")


def test_texto_sin_contexto() -> None:
    texto = FormatoTexto().format(_registro("hola"))
    assert texto.endswith("ia.prueba hola")


def test_hoy_en_la_paz_cruza_la_medianoche_utc() -> None:
    # 2026-11-01 02:00 UTC = 2026-10-31 22:00 en La Paz (UTC−4).
    reloj = RelojFijo(datetime(2026, 11, 1, 2, 0, tzinfo=UTC))
    assert reloj.hoy() == date(2026, 10, 31)
    assert fecha_en_zona(datetime(2026, 11, 1, 4, 0, tzinfo=UTC)) == date(2026, 11, 1)


def test_instantes_iso() -> None:
    momento = leer_instante("2026-10-28T14:30:00Z")
    assert momento == datetime(2026, 10, 28, 14, 30, tzinfo=UTC)
    assert iso_utc(momento) == "2026-10-28T14:30:00Z"
    with pytest.raises(ValueError):
        leer_instante("2026-10-28T14:30:00")
