"""Reporte del agente (502): esquema por agente y validación adicional (obras vistas, evidencias, índices, listas
permitidas y textos sin etiquetas)."""

from typing import Any

import pytest

from ia.agentes.dominio.definiciones import COBROS, MATERIALES, SEGUIMIENTO
from ia.agentes.dominio.reporte import ReporteInvalido, esquema_reporte, validar_reporte

O1 = "b0000000-0000-4000-8000-000000000001"
O2 = "b0000000-0000-4000-8000-000000000002"
VISTAS = {O1: "Edificio Las Palmas", O2: None}
CON_EXITO = {"avance_obra", "buscar_diario"}


def hallazgo(**cambios: Any) -> dict[str, Any]:
    return {
        "obraId": O1,
        "tipo": "ATRASO",
        "severidad": "ALTA",
        "situacion": "Plazo 41 %, avance 32,5 %",
        "causaProbable": "Lluvias de la semana",
        "evidencia": [{"herramienta": "avance_obra", "dato": "diferencia 8,5"}],
        **cambios,
    }


def reporte(hallazgos: list[dict[str, Any]] | None = None, sugerencias: list[dict[str, Any]] | None = None) -> Any:
    return {
        "resumen": "Una obra atrasada.",
        "hallazgos": [hallazgo()] if hallazgos is None else hallazgos,
        "sugerencias": [{"obraId": O1, "accion": "PROGRAMAR_VISITA", "descripcion": "Visitar", "hallazgo": 0}]
        if sugerencias is None
        else sugerencias,
    }


def sugerencia(accion: str = "PROGRAMAR_VISITA", hallazgo: int = 0, obra: str = O1) -> dict[str, Any]:
    return {"obraId": obra, "accion": accion, "descripcion": "x", "hallazgo": hallazgo}


def test_esquema_limitado_a_los_valores_del_agente() -> None:
    esquema = esquema_reporte(COBROS)
    definiciones = esquema["$defs"]
    assert definiciones["HallazgoReporte"]["properties"]["tipo"]["enum"] == list(COBROS.tipos)
    assert definiciones["SugerenciaReporte"]["properties"]["accion"]["enum"] == list(COBROS.acciones)
    assert definiciones["EvidenciaReporte"]["properties"]["herramienta"]["enum"] == list(COBROS.herramientas)
    assert esquema["additionalProperties"] is False
    assert set(esquema["properties"]) == {"resumen", "hallazgos", "sugerencias"}
    assert esquema_reporte(MATERIALES)["$defs"]["HallazgoReporte"]["properties"]["tipo"]["enum"][0] == "STOCK_BAJO"


def test_reporte_valido_con_nombre_puesto_por_el_codigo() -> None:
    validado = validar_reporte(reporte(), SEGUIMIENTO, VISTAS, CON_EXITO)
    assert validado.resumen == "Una obra atrasada."
    assert validado.hallazgos[0].obra_nombre == "Edificio Las Palmas"
    assert validado.sugerencias[0].obra_nombre == "Edificio Las Palmas"
    assert validado.sugerencias[0].hallazgo_indice == 0
    sin_nombre = validar_reporte(reporte([hallazgo(obraId=O2)], []), SEGUIMIENTO, VISTAS, CON_EXITO)
    assert sin_nombre.hallazgos[0].obra_nombre == "Obra sin nombre en los datos"


def test_textos_sin_etiquetas_html() -> None:
    validado = validar_reporte(
        reporte([hallazgo(situacion="<script>alert(1)</script>Atrasada <b>8 puntos</b>")]),
        SEGUIMIENTO,
        VISTAS,
        CON_EXITO,
    )
    assert validado.hallazgos[0].situacion == "alert(1)Atrasada 8 puntos"


@pytest.mark.parametrize(
    ("datos", "fragmento"),
    [
        (reporte(sugerencias=[sugerencia("APROBAR_PLANILLA")]), "accion"),
        (reporte([hallazgo(tipo="COBRO_VENCIDO")]), "tipo"),
        (reporte([hallazgo(obraId="b0000000-0000-4000-8000-0000000000ff")], []), "no salió"),
        (reporte([hallazgo(evidencia=[{"herramienta": "planillas_estado", "dato": "x"}])]), "con éxito"),
        (reporte(sugerencias=[sugerencia(hallazgo=3)]), "no existe"),
        (reporte([hallazgo(), hallazgo(obraId=O2)], [sugerencia(obra=O2)]), "no coincide"),
        (reporte([hallazgo(situacion="<b></b>")]), "vacío"),
        ({**reporte(), "sql": "SELECT 1"}, "sql"),
        (reporte([hallazgo(obraId="x' OR 1=1")]), "obraId"),
        (reporte([hallazgo()] * 11), "hallazgos"),
        (reporte([hallazgo(evidencia=[])]), "evidencia"),
        (reporte([hallazgo(severidad="CRITICA")]), "severidad"),
        ({"resumen": "", "hallazgos": [], "sugerencias": []}, "resumen"),
    ],
)  # fmt: skip
def test_reporte_invalido(datos: Any, fragmento: str) -> None:
    with pytest.raises(ReporteInvalido) as error:
        validar_reporte(datos, SEGUIMIENTO, VISTAS, CON_EXITO)
    assert any(fragmento in e for e in error.value.errores), error.value.errores
