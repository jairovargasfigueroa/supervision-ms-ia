"""Motor de consultas del constructor (503): SQL con el usuario lector y MongoDB, con datos reales en las tablas de la
401 y en `consumo_atipico`."""

from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apoyo import reportes_503 as d
from ia.constructor_reportes.aplicacion.puertos import ConsultaResuelta, FilasConsulta, ReportesNoDisponibles
from ia.constructor_reportes.dominio.catalogo import MCP, REST
from ia.constructor_reportes.dominio.validacion import validar_receta
from ia.constructor_reportes.infraestructura.atipicos_mongo import ConsultaAtipicosMongo
from ia.constructor_reportes.infraestructura.constructor_sql import patron_contiene
from ia.constructor_reportes.infraestructura.ejecutor_sql import EjecutorReportes, EjecutorSql

pytestmark = pytest.mark.integracion


@pytest.fixture
async def ejecutor(
    motor: AsyncEngine, url_lector: str, ia_db: AsyncDatabase[dict[str, Any]]
) -> AsyncIterator[EjecutorReportes]:
    await d.cargar(motor, ia_db)
    lector = create_async_engine(url_lector, pool_size=2)
    sql = EjecutorSql(lector)
    try:
        yield EjecutorReportes(sql, ConsultaAtipicosMongo(ia_db, sql))
    finally:
        await lector.dispose()


async def _consultar(ejecutor: EjecutorReportes, receta: dict[str, Any], modo: str = REST, **ids: Any) -> FilasConsulta:
    return await ejecutor.ejecutar(ConsultaResuelta(validar_receta(receta, modo, d.HOY), d.HOY, **ids))


def _f(campo: str, operador: str, valor: Any) -> dict[str, Any]:
    return {"campo": campo, "operador": operador, "valor": valor}


def test_patron_contiene_escapa_comodines() -> None:
    assert patron_contiene("100% de las obras_activas") == "%100\\% de las obras\\_activas%"
    assert patron_contiene("a\\b") == "%a\\\\b%"


async def test_obras_por_defecto(ejecutor: EjecutorReportes) -> None:
    r = await _consultar(ejecutor, {"conjunto": "OBRAS"})
    assert r.claves == ("obra", "zona", "estado", "avance_pct", "plazo_pct", "diferencia", "en_riesgo")
    assert r.filas == [
        (d.NOMBRE_O2, "SUR", "EN_EJECUCION", Decimal("10.00"), Decimal("30.00"), Decimal("20.00"), True),
        (d.NOMBRE_O1, "NORTE", "EN_EJECUCION", Decimal("32.50"), Decimal("41.00"), Decimal("8.50"), False),
    ]
    assert not r.truncado
    riesgo = await _consultar(
        ejecutor,
        {
            "conjunto": "OBRAS",
            "columnas": ["obra", "riesgo_pct", "nivel_riesgo"],
            "orden": [{"por": "riesgo_pct", "direccion": "DESC"}],
        },
    )
    assert riesgo.filas == [(d.NOMBRE_O1, Decimal("72.0"), "ALTO"), (d.NOMBRE_O2, Decimal("35.0"), "MEDIO")]
    sin_contrato = await _consultar(
        ejecutor,
        {"conjunto": "OBRAS", "columnas": ["obra", "nivel_riesgo"], "filtros": [_f("estado", "IGUAL", "SIN_CONTRATO")]},
    )
    assert sin_contrato.filas == [(d.NOMBRE_O3, "SIN_DATOS")]


async def test_obras_agrupadas_y_truncado(ejecutor: EjecutorReportes) -> None:
    r = await _consultar(
        ejecutor,
        {
            "conjunto": "OBRAS",
            "filtros": [_f("estado", "EN", ["EN_EJECUCION", "SIN_CONTRATO"])],
            "agruparPor": ["zona"],
            "metricas": [{"funcion": "PROMEDIO", "campo": "avance_pct"}, {"funcion": "CONTEO"}],
            "orden": [{"por": "zona", "direccion": "ASC"}],
            "limite": 2,
        },
    )
    assert r.claves == ("zona", "promedio_avance_pct", "conteo")
    assert r.filas == [("ESTE", Decimal("0.00"), 1), ("NORTE", Decimal("32.50"), 1)]
    assert r.truncado


async def test_material_usado_del_mes_por_obra_y_material(ejecutor: EjecutorReportes) -> None:
    r = await _consultar(ejecutor, {"conjunto": "CONSUMO_MATERIAL"})
    assert r.claves == ("obra", "material", "suma_cantidad", "suma_monto", "suma_salidas")
    assert r.filas == [
        (d.NOMBRE_O1, d.CEMENTO, Decimal("1240.000"), Decimal("76880.00"), 31),
        (d.NOMBRE_O1, d.FIERRO, Decimal("100.000"), Decimal("8000.00"), 5),
        (d.NOMBRE_O2, d.CEMENTO, Decimal("50.000"), Decimal("3100.00"), 2),
    ]
    solo_cemento = await _consultar(
        ejecutor,
        {
            "conjunto": "CONSUMO_MATERIAL",
            "filtros": [_f("material", "CONTIENE", "cemento"), _f("periodo", "DESDE", "2027-02")],
        },
        ids_material=(d.CEMENTO,),
    )
    assert [(f[0], f[3]) for f in solo_cemento.filas] == [
        (d.NOMBRE_O1, Decimal("107880.00")),
        (d.NOMBRE_O2, Decimal("3100.00")),
    ]
    por_id = await _consultar(
        ejecutor,
        {"conjunto": "CONSUMO_MATERIAL", "filtros": [_f("materialId", "IGUAL", d.FIERRO)], "agruparPor": ["material"]},
        MCP,
    )
    assert por_id.filas == [(d.FIERRO, Decimal("100.000"), Decimal("8000.00"), 5)]


async def test_movimientos_ultimos_3_meses_con_fecha_en_la_paz(ejecutor: EjecutorReportes) -> None:
    r = await _consultar(ejecutor, {"conjunto": "MOVIMIENTOS"})
    assert [(f[0], f[3]) for f in r.filas] == [
        (date(2027, 3, 15), "SALIDA"),  # 01:30 UTC del 16 = 21:30 del 15 en La Paz
        (date(2027, 3, 10), "SALIDA"),
        (date(2027, 3, 1), "INGRESO"),
        (date(2027, 2, 5), "SALIDA"),
    ]
    sin_stock = await _consultar(
        ejecutor,
        {
            "conjunto": "MOVIMIENTOS",
            "filtros": [_f("tipo", "IGUAL", "SALIDA"), _f("soloSuperaStock", "IGUAL", True)],
            "agruparPor": ["obra", "material", "periodo"],
            "metricas": [
                {"funcion": "CONTEO"},
                {"funcion": "SUMA", "campo": "cantidad"},
                {"funcion": "MAXIMO", "campo": "fecha"},
            ],
        },
    )
    assert sin_stock.filas == [(d.NOMBRE_O1, d.CEMENTO, "2027-03", 2, Decimal("50.000"), date(2027, 3, 15))]


async def test_cobros_solo_con_numero_y_obra_y_dias_para_vencer(ejecutor: EjecutorReportes) -> None:
    todos = await _consultar(ejecutor, {"conjunto": "COBROS"})
    assert [f[0] for f in todos.filas] == ["COB-2027-0003", "COB-2027-0002", "COB-2027-0001"]
    por_vencer = await _consultar(
        ejecutor,
        {
            "conjunto": "COBROS",
            "filtros": [_f("estado", "IGUAL", "PENDIENTE"), _f("venceEnDias", "IGUAL", 15)],
            "columnas": ["numero", "vence_en", "dias_para_vencer"],
        },
    )
    assert por_vencer.filas == [("COB-2027-0002", date(2027, 4, 1), 12)]
    cobrado = await _consultar(
        ejecutor,
        {
            "conjunto": "COBROS",
            "filtros": [_f("periodo", "DESDE", "2027-01")],
            "agruparPor": ["obra"],
            "metricas": [
                {"funcion": "CONTEO"},
                {"funcion": "SUMA", "campo": "monto"},
                {"funcion": "SUMA", "campo": "monto_vencido"},
            ],
            "orden": [{"por": "suma_monto", "direccion": "DESC"}],
        },
    )
    assert cobrado.filas == [
        (d.NOMBRE_O1, 2, Decimal("61881.80"), Decimal("20000.00")),
        (d.NOMBRE_O2, 1, Decimal("5000.00"), Decimal("0.00")),
    ]


async def test_planillas_sin_periodo_y_motivo_recortado(ejecutor: EjecutorReportes) -> None:
    observadas = await _consultar(
        ejecutor,
        {
            "conjunto": "PLANILLAS",
            "filtros": [_f("estado", "IGUAL", "OBSERVADA")],
            "columnas": ["periodo", "ultimo_motivo"],
        },
    )
    assert len(observadas.filas) == 2
    assert len(observadas.filas[0][1]) == 200
    con_periodo = await _consultar(
        ejecutor,
        {"conjunto": "PLANILLAS", "filtros": [_f("estado", "IGUAL", "OBSERVADA"), _f("periodo", "IGUAL", "2027-02")]},
    )
    assert len(con_periodo.filas) == 1
    aprobadas = await _consultar(
        ejecutor,
        {
            "conjunto": "PLANILLAS",
            "filtros": [_f("aprobadaEn", "DESDE", "2027-03-01")],
            "columnas": ["obra", "aprobada_en"],
        },
    )
    assert aprobadas.filas == [(d.NOMBRE_O1, date(2027, 3, 1))]


async def test_contiene_escapa_porcentaje_y_guion_bajo(ejecutor: EjecutorReportes) -> None:
    async def obras(texto: str) -> list[Any]:
        r = await _consultar(
            ejecutor, {"conjunto": "OBRAS", "columnas": ["obra"], "filtros": [_f("obra", "CONTIENE", texto)]}
        )
        return [f[0] for f in r.filas]

    assert await obras("100%") == [d.NOMBRE_O2]
    assert await obras("100% de las obras_activas") == []
    assert await obras("r_A") == [d.NOMBRE_O2]
    assert await obras("s_P") == []  # «_» no es comodín
    assert await obras("'; DROP TABLE rpt_cobro; --") == []
    assert len((await _consultar(ejecutor, {"conjunto": "COBROS"})).filas) == 3


async def test_consumos_atipicos_en_mongo(ejecutor: EjecutorReportes) -> None:
    r = await _consultar(ejecutor, {"conjunto": "CONSUMOS_ATIPICOS"})
    assert r.claves == ("fecha", "obra", "material", "cantidad", "puntaje", "motivos", "estado_revision")
    assert [(f[0], f[1], f[3], f[4]) for f in r.filas] == [
        ("2027-02-20", d.NOMBRE_O2, Decimal("60.5"), 0.80),
        ("2027-03-14", d.NOMBRE_O1, Decimal("120"), 0.71),  # 02:40 UTC del 15 = 22:40 del 14 en La Paz
        ("2027-03-12", d.NOMBRE_O1, Decimal("30"), 0.65),
    ]
    assert r.filas[0][5].startswith("La cantidad es 4,2 veces la habitual; Se registró")
    pendientes_norte = await _consultar(
        ejecutor,
        {
            "conjunto": "CONSUMOS_ATIPICOS",
            "filtros": [_f("estadoRevision", "IGUAL", "PENDIENTE"), _f("zona", "IGUAL", "NORTE")],
        },
    )
    assert [f[1] for f in pendientes_norte.filas] == [d.NOMBRE_O1]
    por_obra = await _consultar(
        ejecutor,
        {
            "conjunto": "CONSUMOS_ATIPICOS",
            "agruparPor": ["obra"],
            "metricas": [
                {"funcion": "CONTEO"},
                {"funcion": "SUMA", "campo": "cantidad"},
                {"funcion": "MAXIMO", "campo": "puntaje"},
            ],
            "orden": [{"por": "obra", "direccion": "ASC"}],
        },
    )
    assert por_obra.filas == [(d.NOMBRE_O2, 1, Decimal("60.5"), 0.80), (d.NOMBRE_O1, 2, Decimal("150"), 0.71)]
    zona_sin_obras = await _consultar(
        ejecutor, {"conjunto": "CONSUMOS_ATIPICOS", "filtros": [_f("zona", "IGUAL", "OESTE")]}
    )
    assert zona_sin_obras.filas == []


async def test_base_caida_es_reportes_no_disponibles() -> None:
    caida = create_async_engine("postgresql+psycopg://nadie:nadie@127.0.0.1:1/ninguna")
    try:
        with pytest.raises(ReportesNoDisponibles):
            await EjecutorSql(caida).ejecutar(
                ConsultaResuelta(validar_receta({"conjunto": "OBRAS"}, REST, d.HOY), d.HOY)
            )
    finally:
        await caida.dispose()
