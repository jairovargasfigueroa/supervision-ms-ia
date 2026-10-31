"""Variables del modelo de riesgo de retraso, niveles y factores (diseño de la 402).

`calcular_variables` es la **misma función** para el generador sintético, el entrenamiento con datos reales y la
predicción del servicio: así no hay diferencias entre entrenar y usar el modelo.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final

# Orden de las variables del modelo (columnas del CSV y del vector de entrada).
VARIABLES: Final = (
    "plazo_pct",
    "avance_pct",
    "brecha",
    "ritmo_reciente",
    "ritmo_requerido",
    "relacion_ritmo",
    "meses_sin_planilla",
    "planillas_observadas",
    "atipicos_90d",
    "supera_stock_90d",
    "plazo_dias",
    "log_monto",
)

# Descripción corta de cada variable (pantalla «Sobre el modelo»).
DESCRIPCIONES: Final[Mapping[str, str]] = {
    "plazo_pct": "Plazo transcurrido (%)",
    "avance_pct": "Avance de la obra (%)",
    "brecha": "Plazo transcurrido − avance (puntos)",
    "ritmo_reciente": "Avance mensual de las últimas 3 planillas aprobadas (puntos por mes)",
    "ritmo_requerido": "Avance mensual necesario para terminar a tiempo (puntos por mes)",
    "relacion_ritmo": "Ritmo reciente / ritmo necesario",
    "meses_sin_planilla": "Meses desde la última planilla aprobada",
    "planillas_observadas": "Veces que se observaron planillas de la obra",
    "atipicos_90d": "Consumos de material atípicos en los últimos 90 días",
    "supera_stock_90d": "Salidas de material sin stock suficiente en los últimos 90 días",
    "plazo_dias": "Plazo del contrato (días)",
    "log_monto": "Logaritmo del monto del contrato",
}

# Plantillas de los textos de los factores (X = valor); los textos reales los arma `texto_factor`.
PLANTILLAS_FACTOR: Final[Mapping[str, str]] = {
    "plazo_pct": "Ya transcurrió X % del plazo",
    "avance_pct": "El avance es de solo X %",
    "brecha": "El avance está X puntos por debajo del plazo transcurrido",
    "ritmo_reciente": "El avance mensual reciente es bajo (X puntos por mes)",
    "ritmo_requerido": "Para terminar a tiempo se necesitan X puntos por mes",
    "relacion_ritmo": "El ritmo de avance de los últimos meses es menor al necesario para terminar a tiempo",
    "meses_sin_planilla": "Hace X meses que no se aprueba una planilla",
    "planillas_observadas": "Se observaron X planillas",
    "atipicos_90d": "Hubo X consumos de material atípicos en los últimos 90 días",
    "supera_stock_90d": "Hubo X salidas de material sin stock suficiente",
    "plazo_dias": "El plazo de la obra es largo (X días)",
    "log_monto": "La obra es de monto alto",
}

DIAS_POR_MES: Final = 30.44
MESES_MINIMOS: Final = 0.5
RELACION_MAXIMA: Final = 3.0
RECIENTES: Final = 3

# Niveles (decisión del equipo): BAJO < 0,30 ≤ MEDIO < 0,60 ≤ ALTO.
UMBRAL_MEDIO: Final = 0.30
UMBRAL_ALTO: Final = 0.60
BAJO: Final = "BAJO"
MEDIO: Final = "MEDIO"
ALTO: Final = "ALTO"
SIN_DATOS: Final = "SIN_DATOS"
PLAZO_MINIMO_PCT: Final = 5.0

# Factores: hasta 3 variables con contribución positiva mayor a 0,05 en la escala del modelo.
MAXIMO_FACTORES: Final = 3
CONTRIBUCION_MINIMA: Final = 0.05


@dataclass(frozen=True)
class PlanillaAprobadaObra:
    periodo: str
    """`AAAA-MM`."""
    avance_periodo_pct: float


@dataclass(frozen=True)
class EstadoObra:
    """Lo que se sabe de la obra el día del cálculo (base de reportes de la 401 y consumos atípicos de la 403)."""

    fecha_inicio: date
    plazo_dias: int
    monto_contrato: float
    avance_pct: float
    plazo_pct: float
    planillas_aprobadas: tuple[PlanillaAprobadaObra, ...]
    planillas_observadas: int
    atipicos_90d: int
    supera_stock_90d: int


@dataclass(frozen=True)
class Factor:
    variable: str
    descripcion: str
    peso: float


def calcular_variables(estado_obra: EstadoObra, hoy: date) -> dict[str, float]:
    """Las 12 variables del modelo, en el orden de `VARIABLES`."""
    plazo = float(estado_obra.plazo_pct)
    avance = float(estado_obra.avance_pct)
    aprobadas = sorted(estado_obra.planillas_aprobadas, key=lambda p: p.periodo)
    recientes = aprobadas[-RECIENTES:]
    ritmo_reciente = sum(p.avance_periodo_pct for p in recientes) / len(recientes) if recientes else 0.0
    meses_restantes = estado_obra.plazo_dias * (1 - plazo / 100) / DIAS_POR_MES
    ritmo_requerido = max(100 - avance, 0.0) / max(meses_restantes, MESES_MINIMOS)
    relacion = RELACION_MAXIMA if ritmo_requerido == 0 else min(ritmo_reciente / ritmo_requerido, RELACION_MAXIMA)
    if aprobadas:
        anio, mes = (int(parte) for parte in aprobadas[-1].periodo.split("-"))
    else:
        anio, mes = estado_obra.fecha_inicio.year, estado_obra.fecha_inicio.month
    meses_sin_planilla = max((hoy.year * 12 + hoy.month) - (anio * 12 + mes), 0)
    return {
        "plazo_pct": plazo,
        "avance_pct": avance,
        "brecha": plazo - avance,
        "ritmo_reciente": ritmo_reciente,
        "ritmo_requerido": ritmo_requerido,
        "relacion_ritmo": relacion,
        "meses_sin_planilla": float(meses_sin_planilla),
        "planillas_observadas": float(estado_obra.planillas_observadas),
        "atipicos_90d": float(estado_obra.atipicos_90d),
        "supera_stock_90d": float(estado_obra.supera_stock_90d),
        "plazo_dias": float(estado_obra.plazo_dias),
        "log_monto": math.log(estado_obra.monto_contrato) if estado_obra.monto_contrato > 0 else 0.0,
    }


def sin_datos(estado: str, plazo_pct: float | None) -> bool:
    """`SIN_DATOS`: obra sin contrato (no `EN_EJECUCION`) o con menos de 5 % de plazo transcurrido."""
    return estado != "EN_EJECUCION" or plazo_pct is None or float(plazo_pct) < PLAZO_MINIMO_PCT


def nivel(probabilidad: float) -> str:
    if probabilidad >= UMBRAL_ALTO:
        return ALTO
    if probabilidad >= UMBRAL_MEDIO:
        return MEDIO
    return BAJO


def elegir_factores(variables: Mapping[str, float], contribuciones: Mapping[str, float]) -> list[Factor]:
    """Las variables que más suben el riesgo (contribución > 0,05), de mayor a menor, como mucho 3."""
    positivas = sorted(
        (
            (nombre, peso)
            for nombre, peso in contribuciones.items()
            if nombre in VARIABLES and peso > CONTRIBUCION_MINIMA
        ),
        key=lambda par: par[1],
        reverse=True,
    )
    return [
        Factor(variable=nombre, descripcion=texto_factor(nombre, variables[nombre]), peso=round(peso, 2))
        for nombre, peso in positivas[:MAXIMO_FACTORES]
    ]


def texto_factor(variable: str, valor: float) -> str:
    """Texto simple del factor (diseño de la 402, tabla de variables)."""
    n = round(valor)
    match variable:
        case "plazo_pct":
            return f"Ya transcurrió {_numero(valor)} % del plazo"
        case "avance_pct":
            return f"El avance es de solo {_numero(valor)} %"
        case "brecha":
            return f"El avance está {_numero(valor)} puntos por debajo del plazo transcurrido"
        case "ritmo_reciente":
            return f"El avance mensual reciente es bajo ({_numero(valor)} puntos por mes)"
        case "ritmo_requerido":
            return f"Para terminar a tiempo se necesitan {_numero(valor)} puntos por mes"
        case "relacion_ritmo":
            return "El ritmo de avance de los últimos meses es menor al necesario para terminar a tiempo"
        case "meses_sin_planilla":
            return f"Hace {n} {'mes' if n == 1 else 'meses'} que no se aprueba una planilla"
        case "planillas_observadas":
            return "Se observó 1 planilla" if n == 1 else f"Se observaron {n} planillas"
        case "atipicos_90d":
            if n == 1:
                return "Hubo 1 consumo de material atípico en los últimos 90 días"
            return f"Hubo {n} consumos de material atípicos en los últimos 90 días"
        case "supera_stock_90d":
            if n == 1:
                return "Hubo 1 salida de material sin stock suficiente"
            return f"Hubo {n} salidas de material sin stock suficiente"
        case "plazo_dias":
            return f"El plazo de la obra es largo ({n} días)"
        case "log_monto":
            return "La obra es de monto alto"
    raise ValueError(f"Variable desconocida: {variable}")


def _numero(valor: float) -> str:
    """Un decimal con coma (`33,3`); sin decimal si es entero (`50`)."""
    redondeado = round(valor, 1)
    if redondeado == int(redondeado):
        return str(int(redondeado))
    return f"{redondeado:.1f}".replace(".", ",")
