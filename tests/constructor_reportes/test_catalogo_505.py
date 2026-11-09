"""505: catálogo ampliado. Filtros «es uno de» por nombre, numéricos y de fecha; riesgo de la obra en todos los
conjuntos; datos de otros temas por obra (subconsultas escalares) contra PostgreSQL real."""

from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from apoyo import reportes_503 as d
from ia.constructor_reportes.aplicacion.motor import Directorio, MotorReportes
from ia.constructor_reportes.aplicacion.puertos import ConsultaResuelta, FilasConsulta, MaterialCatalogo
from ia.constructor_reportes.dominio.catalogo import CATALOGO, REST, describir_catalogo
from ia.constructor_reportes.dominio.validacion import RecetaInvalida, validar_receta
from ia.constructor_reportes.infraestructura.atipicos_mongo import ConsultaAtipicosMongo
from ia.constructor_reportes.infraestructura.ejecutor_sql import EjecutorReportes, EjecutorSql


def _f(campo: str, operador: str, valor: Any) -> dict[str, Any]:
    return {"campo": campo, "operador": operador, "valor": valor}


# --- Validación ---------------------------------------------------------------------------------------------------


def test_filtro_numerico_acepta_numeros_y_texto_numerico() -> None:
    receta = validar_receta(
        {"conjunto": "OBRAS", "filtros": [_f("avancePct", "DESDE", 10), _f("montoVencido", "HASTA", "2500,5")]},
        REST,
        d.HOY,
    )
    assert (receta.filtros_de("avancePct")[0].valor, receta.filtros_de("montoVencido")[0].valor) == (10, 2500.5)
    with pytest.raises(RecetaInvalida, match="número"):
        validar_receta({"conjunto": "OBRAS", "filtros": [_f("avancePct", "DESDE", "mucho")]}, REST, d.HOY)


def test_numeros_no_tienen_rango_de_meses_ni_desde_implicito() -> None:
    receta = validar_receta({"conjunto": "COBROS", "filtros": [_f("monto", "HASTA", 50000)]}, REST, d.HOY)
    assert [(f.campo, f.operador) for f in receta.filtros] == [("monto", "HASTA")]


def test_vencimiento_acepta_fechas_futuras_y_la_emision_no() -> None:
    receta = validar_receta({"conjunto": "COBROS", "filtros": [_f("venceEn", "HASTA", "2027-04-30")]}, REST, d.HOY)
    assert receta.filtros_de("venceEn")[0].valor.isoformat() == "2027-04-30"  # type: ignore[union-attr]
    with pytest.raises(RecetaInvalida, match="futuro"):
        validar_receta({"conjunto": "COBROS", "filtros": [_f("emitidoEn", "DESDE", "2027-04-30")]}, REST, d.HOY)


def test_nombres_aceptan_es_uno_de() -> None:
    receta = validar_receta(
        {"conjunto": "MOVIMIENTOS", "filtros": [_f("material", "EN", ["arena", "grava", "arena"])]}, REST, d.HOY
    )
    assert receta.filtros_de("material")[0].valor == ("arena", "grava")


def test_riesgo_de_la_obra_en_todos_los_conjuntos() -> None:
    for conjunto in CATALOGO.values():
        claves = {f.clave for f in conjunto.filtros}
        assert {"nivelRiesgo", "soloEnRiesgo", "zona"} <= claves, conjunto.conjunto


def test_el_catalogo_describe_los_filtros_nuevos() -> None:
    texto = describir_catalogo(REST)
    assert "material [CONTIENE/EN: texto de 2 a 100; con EN, lista de textos]" in texto
    assert "venceEn [DESDE/HASTA: AAAA-MM-DD, puede ser futura]" in texto
    assert "planillasObservadas [DESDE/HASTA: número]" in texto


# --- Nombres con «es uno de» en el motor --------------------------------------------------------------------------

ARENA, GRAVA, CEMENTO = "m1", "m2", "m3"
CATALOGO_MATERIALES = {
    ARENA: MaterialCatalogo(ARENA, "ARE-01", "Arena fina", "m3"),
    GRAVA: MaterialCatalogo(GRAVA, "GRA-34", "Grava de 3/4", "m3"),
    CEMENTO: MaterialCatalogo(CEMENTO, "CEM-IP30", "Cemento IP-30", "bolsa"),
}


class _Ejecutor:
    def __init__(self) -> None:
        self.consultas: list[ConsultaResuelta] = []

    async def ejecutar(self, consulta: ConsultaResuelta) -> FilasConsulta:
        self.consultas.append(consulta)
        return FilasConsulta(consulta.receta.claves_salida(), [], False)


class _Catalogo:
    async def materiales(self, token: str) -> Any:
        return CATALOGO_MATERIALES


async def test_motor_resuelve_es_uno_de_por_nombre_y_codigo() -> None:
    ejecutor = _Ejecutor()
    motor = MotorReportes(ejecutor, _Catalogo())
    receta = validar_receta(
        {"conjunto": "MOVIMIENTOS", "filtros": [_f("material", "EN", ["arena", "GRA-34"])]}, REST, d.HOY
    )
    await motor.ejecutar(receta, d.HOY, token="t")
    assert ejecutor.consultas[0].ids_material == (ARENA, GRAVA)

    directorio = Directorio({"c1": "Constructora Andina SRL", "c2": "Inmobiliaria Sur", "c3": "Otro"})
    receta = validar_receta({"conjunto": "COBROS", "filtros": [_f("cliente", "EN", ["andina", "sur"])]}, REST, d.HOY)
    await motor.ejecutar(receta, d.HOY, directorio=directorio, token="t")
    assert ejecutor.consultas[1].ids_cliente == ("c1", "c2")


# --- SQL real -----------------------------------------------------------------------------------------------------


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


async def _consultar(ejecutor: EjecutorReportes, receta: dict[str, Any]) -> FilasConsulta:
    return await ejecutor.ejecutar(ConsultaResuelta(validar_receta(receta, REST, d.HOY), d.HOY))


@pytest.mark.integracion
async def test_obras_con_datos_de_otros_temas(ejecutor: EjecutorReportes) -> None:
    columnas = [
        "obra",
        "planillas_observadas",
        "planillas_aprobadas",
        "cobros_vencidos",
        "monto_vencido",
        "monto_por_cobrar",
        "salidas_sin_stock",
    ]
    r = await _consultar(
        ejecutor, {"conjunto": "OBRAS", "columnas": columnas, "orden": [{"por": "obra", "direccion": "ASC"}]}
    )
    filas = {fila[0]: fila[1:] for fila in r.filas}
    # O1: 1 aprobada; COB-0001 vencido (20000) y COB-0002 pendiente; 2 salidas que superaron el stock.
    assert filas[d.NOMBRE_O1] == (0, 1, 1, Decimal("20000.00"), Decimal("61881.80"), 2)
    # O2: 2 observadas; un cobro pendiente con número (el sin número no cuenta); 1 salida sin stock.
    assert filas[d.NOMBRE_O2] == (2, 0, 0, 0, Decimal("5000.00"), 1)


@pytest.mark.integracion
async def test_obras_que_cruzan_temas_y_agregadas(ejecutor: EjecutorReportes) -> None:
    r = await _consultar(
        ejecutor,
        {
            "conjunto": "OBRAS",
            "columnas": ["obra"],
            "filtros": [_f("planillasObservadas", "DESDE", 1), _f("nivelRiesgo", "EN", ["ALTO", "MEDIO"])],
        },
    )
    assert r.filas == [(d.NOMBRE_O2,)]
    agrupado = await _consultar(
        ejecutor,
        {
            "conjunto": "OBRAS",
            "agruparPor": ["zona"],
            "metricas": [{"funcion": "SUMA", "campo": "monto_por_cobrar"}],
            "orden": [{"por": "zona", "direccion": "ASC"}],
        },
    )
    assert agrupado.filas == [("NORTE", Decimal("61881.80")), ("SUR", Decimal("5000.00"))]


@pytest.mark.integracion
async def test_obra_es_uno_de_y_filtros_numericos_y_de_fecha(ejecutor: EjecutorReportes) -> None:
    r = await _consultar(
        ejecutor,
        {
            "conjunto": "OBRAS",
            "columnas": ["obra"],
            "filtros": [_f("obra", "EN", [d.NOMBRE_O1[:6], d.NOMBRE_O2[-6:]]), _f("avancePct", "DESDE", 20)],
        },
    )
    assert r.filas == [(d.NOMBRE_O1,)]
    cobros = await _consultar(
        ejecutor,
        {
            "conjunto": "COBROS",
            "columnas": ["numero"],
            "filtros": [_f("monto", "DESDE", 10000), _f("venceEn", "HASTA", "2027-04-30")],
            "orden": [{"por": "numero", "direccion": "ASC"}],
        },
    )
    assert cobros.filas == [("COB-2027-0001",), ("COB-2027-0002",)]
    vencidos = await _consultar(
        ejecutor, {"conjunto": "COBROS", "columnas": ["numero"], "filtros": [_f("diasParaVencer", "HASTA", 0)]}
    )
    assert vencidos.filas == [("COB-2027-0001",)]


@pytest.mark.integracion
async def test_riesgo_de_la_obra_en_movimientos_y_planillas(ejecutor: EjecutorReportes) -> None:
    movimientos = await _consultar(
        ejecutor,
        {
            "conjunto": "MOVIMIENTOS",
            "columnas": ["obra", "cantidad"],
            "filtros": [_f("nivelRiesgo", "IGUAL", "ALTO"), _f("cantidad", "DESDE", 40)],
        },
    )
    assert [fila[0] for fila in movimientos.filas] == [d.NOMBRE_O1, d.NOMBRE_O1]
    planillas = await _consultar(
        ejecutor, {"conjunto": "PLANILLAS", "columnas": ["obra"], "filtros": [_f("soloEnRiesgo", "IGUAL", True)]}
    )
    assert {fila[0] for fila in planillas.filas} == {d.NOMBRE_O2}
