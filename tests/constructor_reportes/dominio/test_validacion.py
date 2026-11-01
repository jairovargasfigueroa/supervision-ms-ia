"""`validar_receta`, valores por defecto, atajos y lo aplicado (dominio puro, sin base)."""

from datetime import date
from typing import Any

import pytest

from ia.constructor_reportes.dominio.atajos import ATAJOS
from ia.constructor_reportes.dominio.catalogo import CATALOGO, MCP, REST
from ia.constructor_reportes.dominio.resultado import (
    Resultado,
    describir_aplicado,
    resumen_plantilla,
    totales,
    valor_texto,
)
from ia.constructor_reportes.dominio.validacion import (
    Columna,
    FiltroValidado,
    IntencionNoReconocida,
    MetricaValidada,
    RecetaInvalida,
    validar_receta,
)

HOY = date(2027, 3, 20)


def _invalida(receta: dict[str, Any], modo: str = REST) -> list[tuple[str, str]]:
    with pytest.raises(RecetaInvalida) as error:
        validar_receta(receta, modo, HOY)
    return [(e.campo, e.mensaje) for e in error.value.errores]


def test_valores_por_defecto_de_obras() -> None:
    r = validar_receta({"conjunto": "OBRAS"}, REST, HOY)
    assert r.columnas == ("obra", "zona", "estado", "avance_pct", "plazo_pct", "diferencia", "en_riesgo")
    assert r.filtros == (FiltroValidado("estado", "IGUAL", "EN_EJECUCION"),)
    assert r.orden == (("diferencia", "DESC"), ("obra", "ASC"))
    assert r.limite == 100
    assert r.como_json()["filtros"] == [{"campo": "estado", "operador": "IGUAL", "valor": "EN_EJECUCION"}]


def test_consumo_material_siempre_agrupado_con_metricas_y_mes_actual() -> None:
    r = validar_receta({"conjunto": "CONSUMO_MATERIAL"}, REST, HOY)
    assert r.agrupar_por == ("obra", "material")
    assert r.metricas == (
        MetricaValidada("SUMA", "cantidad"),
        MetricaValidada("SUMA", "monto"),
        MetricaValidada("SUMA", "salidas"),
    )
    assert r.filtros == (FiltroValidado("periodo", "IGUAL", "2027-03"),)
    assert r.orden == (("suma_monto", "DESC"),)
    assert [c.clave for c in r.columnas_salida()] == [
        "obra",
        "material",
        "unidad",
        "suma_cantidad",
        "suma_monto",
        "suma_salidas",
    ]
    assert r.columnas_salida()[4] == Columna("suma_monto", "Total Monto referencial", "MONTO")
    assert _invalida({"conjunto": "CONSUMO_MATERIAL", "columnas": ["obra"]})[0][0] == "agruparPor"


def test_movimientos_ultimos_3_meses_salvo_con_fecha() -> None:
    assert validar_receta({"conjunto": "MOVIMIENTOS"}, REST, HOY).filtros == (
        FiltroValidado("periodo", "DESDE", "2027-01"),
    )
    con_fecha = validar_receta(
        {"conjunto": "MOVIMIENTOS", "filtros": [{"campo": "fecha", "operador": "DESDE", "valor": "2027-03-01"}]},
        REST,
        HOY,
    )
    assert con_fecha.filtros == (FiltroValidado("fecha", "DESDE", date(2027, 3, 1)),)


def test_titulos_de_metricas_de_fecha_y_conteo() -> None:
    r = validar_receta(
        {
            "conjunto": "MOVIMIENTOS",
            "agruparPor": ["obra"],
            "metricas": [{"funcion": "CONTEO"}, {"funcion": "MAXIMO", "campo": "fecha"}],
        },
        MCP,
        HOY,
    )
    assert [(c.clave, c.titulo, c.tipo) for c in r.columnas_salida()] == [
        ("obra", "Obra", "TEXTO"),
        ("conteo", "Cantidad", "NUMERO"),
        ("maximo_fecha", "Última Fecha", "FECHA"),
    ]
    assert r.orden == (("conteo", "DESC"),)  # sin orden del conjunto que aplique: la primera métrica
    assert r.limite == 20  # MCP


@pytest.mark.parametrize(
    "receta",
    [
        {"conjunto": "USUARIOS"},
        {"conjunto": "COBROS", "sql": "SELECT 1"},
        {"conjunto": "COBROS", "limite": 1000},
        {"conjunto": "COBROS", "limite": 100000},
        {"conjunto": "NINGUNO"},
        {
            "conjunto": "CONSUMOS_ATIPICOS",
            "filtros": [{"campo": "estadoRevision", "operador": "IGUAL", "valor": {"$ne": None}}],
        },
        {"conjunto": "OBRAS", "filtros": [{"campo": "zona", "operador": "EN", "valor": ["N"] * 21}]},
        {"conjunto": "OBRAS", "orden": [{"por": "obra", "direccion": "ARRIBA"}]},
        "no es un objeto",
    ],
)
def test_forma_invalida_o_ninguno_es_intencion_no_reconocida(receta: Any) -> None:
    with pytest.raises(IntencionNoReconocida):
        validar_receta(receta, REST, HOY)


def test_receta_invalida_con_rutas() -> None:
    assert _invalida(
        {"conjunto": "CONSUMO_MATERIAL", "filtros": [{"campo": "estado", "operador": "IGUAL", "valor": "X"}]}
    ) == [("filtros[0].campo", "«estado» no es un filtro de CONSUMO_MATERIAL.")]
    assert _invalida({"conjunto": "OBRAS", "columnas": ["numero"]})[0][0] == "columnas[0]"
    assert (
        _invalida({"conjunto": "OBRAS", "filtros": [{"campo": "zona", "operador": "CONTIENE", "valor": "NORTE"}]})[0][0]
        == "filtros[0].operador"
    )
    assert (
        _invalida({"conjunto": "OBRAS", "orden": [{"por": "nombre; DROP TABLE rpt_obra_resumen"}]})[0][0]
        == "orden[0].por"
    )
    assert _invalida({"conjunto": "OBRAS", "columnas": ["obra"], "orden": [{"por": "zona"}]})[0][0] == "orden[0].por"
    assert (
        _invalida({"conjunto": "OBRAS", "filtros": [{"campo": "zona", "operador": "IGUAL", "valor": "NORESTE"}]})[0][0]
        == "filtros[0].valor"
    )
    assert _invalida({"conjunto": "OBRAS", "metricas": [{"funcion": "CONTEO"}]})[0][0] == "metricas"
    assert _invalida({"conjunto": "PLANILLAS", "agruparPor": ["ultimo_motivo"]})[0][0] == "agruparPor[0]"
    assert (
        _invalida({"conjunto": "OBRAS", "agruparPor": ["zona"], "metricas": [{"funcion": "CONTEO", "campo": "obra"}]})[
            0
        ][0]
        == "metricas[0].campo"
    )
    assert _invalida(
        {"conjunto": "MOVIMIENTOS", "agruparPor": ["obra"], "metricas": [{"funcion": "PROMEDIO", "campo": "cantidad"}]}
    )
    assert _invalida({"conjunto": "COBROS", "filtros": [{"campo": "venceEnDias", "operador": "IGUAL", "valor": 91}]})
    assert _invalida({"conjunto": "OBRAS", "filtros": [{"campo": "soloEnRiesgo", "operador": "IGUAL", "valor": False}]})
    assert _invalida({"conjunto": "OBRAS", "filtros": [{"campo": "obra", "operador": "CONTIENE", "valor": "x"}]})


def test_periodos_futuros_invertidos_o_largos() -> None:
    def periodo(*filtros: tuple[str, str]) -> dict[str, Any]:
        return {
            "conjunto": "PLANILLAS",
            "filtros": [{"campo": "periodo", "operador": op, "valor": v} for op, v in filtros],
        }

    assert _invalida(periodo(("IGUAL", "2027-04")))[0] == (
        "filtros[0].valor",
        "El periodo no puede estar en el futuro.",
    )
    assert _invalida(periodo(("IGUAL", "2027-13")))[0][1] == "El periodo tiene el formato AAAA-MM."
    assert _invalida(periodo(("DESDE", "2027-02"), ("HASTA", "2027-01")))[0][1] == "«DESDE» es posterior a «HASTA»."
    assert _invalida(periodo(("DESDE", "2025-03")))[0][1] == "El rango es de hasta 24 meses."
    validar_receta(periodo(("DESDE", "2025-04")), REST, HOY)
    fecha = {"conjunto": "MOVIMIENTOS", "filtros": [{"campo": "fecha", "operador": "HASTA", "valor": "2027-03-21"}]}
    assert _invalida(fecha)[0][1] == "La fecha no puede estar en el futuro."


def test_modo_mcp_usa_ids_y_el_rest_nombres() -> None:
    nombre = {"campo": "material", "operador": "CONTIENE", "valor": "cemento"}
    cliente = {"campo": "cliente", "operador": "CONTIENE", "valor": "Andina"}
    errores = _invalida({"conjunto": "CONSUMO_MATERIAL", "filtros": [nombre, cliente]}, MCP)
    assert [e[0] for e in errores] == ["filtros[0].campo", "filtros[1].campo"]
    assert "en el modo MCP" in errores[0][1]
    ids = [
        {"campo": "materialId", "operador": "IGUAL", "valor": "e0000000-0000-4000-8000-000000000001"},
        {"campo": "clienteId", "operador": "EN", "valor": ["a0000000-0000-4000-8000-000000000007"]},
        {"campo": "obraId", "operador": "IGUAL", "valor": "b0000000-0000-4000-8000-000000000001"},
    ]
    validar_receta({"conjunto": "CONSUMO_MATERIAL", "filtros": ids}, MCP, HOY)
    assert _invalida({"conjunto": "CONSUMO_MATERIAL", "filtros": ids[1:]}, REST)
    assert _invalida(
        {"conjunto": "OBRAS", "filtros": [{"campo": "obraId", "operador": "IGUAL", "valor": "x' OR 1=1"}]}, MCP
    )
    assert _invalida({"conjunto": "OBRAS", "limite": 60}, MCP) == [("limite", "El límite va de 1 a 50.")]


@pytest.mark.parametrize("atajo", ATAJOS, ids=lambda a: a.atajo)
def test_cada_atajo_pasa_la_validacion(atajo: Any) -> None:
    r = validar_receta(atajo.receta(HOY), REST, HOY)
    esperadas = {
        "AVANCE_OBRAS": ["obra", "zona", "estado", "avance_pct", "plazo_pct", "diferencia", "en_riesgo"],
        "OBRAS_EN_RIESGO": ["obra", "zona", "avance_pct", "plazo_pct", "riesgo_pct", "nivel_riesgo"],
        "CONSUMO_MATERIAL_MES": ["obra", "material", "unidad", "suma_cantidad", "suma_monto", "suma_salidas"],
        "SALIDAS_SIN_STOCK": ["obra", "material", "unidad", "periodo", "conteo", "suma_cantidad", "maximo_fecha"],
        "COBROS_VENCIDOS": ["numero", "obra", "cliente", "monto", "vence_en"],
        "COBROS_POR_VENCER": ["numero", "obra", "monto", "vence_en", "dias_para_vencer"],
        "COBRADO_POR_OBRA": ["obra", "conteo", "suma_monto", "suma_monto_vencido"],
        "PLANILLAS_OBSERVADAS": ["obra", "cliente", "periodo", "veces_observada", "ultimo_origen", "ultimo_motivo"],
        "PLANILLAS_APROBADAS": ["obra", "periodo", "neto", "avance_periodo_pct", "aprobada_en"],
        "CONSUMOS_ATIPICOS_PENDIENTES": [
            "fecha",
            "obra",
            "material",
            "unidad",
            "cantidad",
            "puntaje",
            "motivos",
            "estado_revision",
        ],
    }
    assert [c.clave for c in r.columnas_salida()] == esperadas[atajo.atajo]
    assert r.conjunto in CATALOGO


def test_aplicado_y_plantilla() -> None:
    r = validar_receta(
        {
            "conjunto": "CONSUMO_MATERIAL",
            "filtros": [
                {"campo": "obra", "operador": "CONTIENE", "valor": "Las Palmas"},
                {"campo": "material", "operador": "CONTIENE", "valor": "cemento"},
                {"campo": "periodo", "operador": "DESDE", "valor": "2027-01"},
                {"campo": "periodo", "operador": "HASTA", "valor": "2027-03"},
            ],
        },
        REST,
        HOY,
    )
    aplicado = [(e.nombre, e.valor) for e in describir_aplicado(r)]
    assert aplicado == [
        ("Obra", "contiene «Las Palmas»"),
        ("Material", "contiene «cemento»"),
        ("Periodo", "2027-01 a 2027-03"),
        ("Agrupado por", "Obra, Material"),
        ("Métricas", "Total Cantidad, Total Monto referencial, Total Salidas"),
        ("Orden", "Total Monto referencial (mayor a menor)"),
    ]
    texto = resumen_plantilla("Material usado", describir_aplicado(r), 2)
    assert texto == (
        "Se encontraron 2 filas para «Material usado» con los filtros: Obra: contiene «Las Palmas»; "
        "Material: contiene «cemento»; Periodo: 2027-01 a 2027-03."
    )
    assert resumen_plantilla("Cobros", [], 0) == "No hay datos para «Cobros» con los filtros: ninguno."


def test_totales_de_montos_y_numeros() -> None:
    resultado = Resultado(
        [Columna("obra", "Obra", "TEXTO"), Columna("suma_monto", "Total Monto", "MONTO")],
        [["A", "10.50"], ["B", "4.25"], ["C", None]],
        truncado=False,
    )
    assert totales(resultado) == {"filas": "3", "suma de Total Monto": "14.75"}


def test_valores_como_texto() -> None:
    from decimal import Decimal

    assert valor_texto(Decimal("1240.000")) == "1240.000"
    assert valor_texto(Decimal("1E+3")) == "1000"
    assert valor_texto(0.7134567) == "0.7135"
    assert valor_texto(True) == "true"
    assert valor_texto(date(2027, 3, 1)) == "2027-03-01"
    assert valor_texto(31) == "31"
    assert valor_texto(None) is None
