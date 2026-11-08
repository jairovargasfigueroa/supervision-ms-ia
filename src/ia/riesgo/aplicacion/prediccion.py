"""Cálculo de la predicción de una obra: `SIN_DATOS` o probabilidad, nivel y hasta 3 factores."""

from datetime import date, datetime

from ia.riesgo.aplicacion.puertos import ModeloRiesgo, ObraParaRiesgo, Prediccion
from ia.riesgo.dominio.variables import SIN_DATOS, calcular_variables, elegir_factores, nivel, sin_datos


def calcular_prediccion(
    modelo: ModeloRiesgo, obra: ObraParaRiesgo, hoy: date, motivo: str, clave: str, ahora: datetime
) -> Prediccion:
    if sin_datos(obra.estado, obra.plazo_pct) or obra.estado_obra is None:
        return Prediccion(
            clave=clave,
            obra_id=obra.obra_id,
            riesgo_retraso=None,
            nivel=SIN_DATOS,
            factores=(),
            variables={},
            version_modelo=modelo.info.version,
            motivo=motivo,
            calculada_en=ahora,
        )
    variables = calcular_variables(obra.estado_obra, hoy)
    probabilidad, contribuciones = modelo.predecir(variables)
    probabilidad = round(probabilidad, 4)
    return Prediccion(
        clave=clave,
        obra_id=obra.obra_id,
        riesgo_retraso=probabilidad,
        nivel=nivel(probabilidad),
        factores=tuple(elegir_factores(variables, contribuciones)),
        variables={nombre: round(valor, 4) for nombre, valor in variables.items()},
        version_modelo=modelo.info.version,
        motivo=motivo,
        calculada_en=ahora,
    )
