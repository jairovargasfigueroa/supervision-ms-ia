"""Variables de una salida de material para el Isolation Forest (403, diseño «Variables»).

Son las mismas para entrenar (generador sintético y salidas reales) y para evaluar en el worker: las tres partes
llaman a `calcular_variables_salida`. El historial son solo salidas **anteriores** a la evaluada (nunca ella misma).

Decisiones donde el diseño calla (documentadas aquí y en el README):

- La mediana «del material en todas las obras» usa las últimas 200 salidas del material (`VENTANA_MATERIAL`), igual que
  la de la obra usa las últimas 50: así el generador y el worker hacen el mismo cálculo en tiempo acotado.
- Con `MAD = 0` (todas las cantidades iguales), `z_obra_material` es 0 si la cantidad es igual a la mediana y ±10 si no.
- `razon_periodo`: el «promedio mensual de los periodos anteriores» es el promedio de los periodos anteriores en los
  que hubo salidas de ese material en la obra.
- Sin salida anterior del material en la obra, `log_horas_anterior` usa el máximo (720 h).

Variables agregadas por decisión de Jairo (31/10/2026) para describir las ráfagas, que con las del diseño no se
distinguían de un día normal con varias salidas: `salidas_1h` y `salidas_3h` (salidas del mismo material en la obra en
la última hora y en las últimas 3 horas, incluida esta).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import mean, median
from zoneinfo import ZoneInfo

ZONA_NEGOCIO = "America/La_Paz"

VENTANA_OBRA_MATERIAL = 50
"""Últimas salidas de la misma obra y material para la mediana y el MAD."""
VENTANA_MATERIAL = 200
"""Últimas salidas del material en todas las obras (decisión del equipo, 403)."""
MINIMO_GRUPO = 5
"""Con menos salidas que esto, el grupo no alcanza para la mediana robusta."""
ESCALA_MAD = 1.4826
LIMITE_Z = 10.0
MAXIMO_RAZON_PERIODO = 10.0
MAXIMO_HORAS_ANTERIOR = 720.0
HORA_INICIO_HABITUAL = 6
"""Desde las 06:00 (hora local) es horario habitual."""
HORA_FIN_HABITUAL = 20
"""Desde las 20:00 (hora local) es fuera de horario (decisión del equipo)."""
DOMINGO = 6
"""`datetime.weekday()` del domingo (el sábado se trabaja)."""

NOMBRES_VARIABLES: tuple[str, ...] = (
    "log_cantidad",
    "z_obra_material",
    "razon_material",
    "razon_periodo",
    "hora_sin",
    "hora_cos",
    "fuera_horario",
    "domingo",
    "supera_stock",
    "log_horas_anterior",
    "salidas_dia",
    "salidas_1h",
    "salidas_3h",
)
"""Orden de las columnas del modelo (entrenar y evaluar)."""


@dataclass(frozen=True)
class SalidaMaterial:
    """La `SALIDA` que se evalúa (de `MovimientoRegistrado` o de una fila de `rpt_movimiento`)."""

    movimiento_id: str
    obra_id: str
    material_id: str
    cantidad: float
    periodo: str
    """`AAAA-MM` (el de Materiales, en la zona del negocio)."""
    supera_stock: bool
    registrado_en: datetime
    """Con zona."""


@dataclass(frozen=True)
class SalidaPrevia:
    cantidad: float
    registrado_en: datetime


@dataclass(frozen=True)
class Historial:
    """Salidas anteriores a la evaluada (sin ella)."""

    obra_material: Sequence[SalidaPrevia] = ()
    """Últimas salidas de la misma obra y material, de la más antigua a la más reciente (se usan las 50 últimas)."""
    material: Sequence[float] = ()
    """Cantidades de las últimas salidas del material en todas las obras, de la más antigua a la más reciente (se usan
    las 200 últimas)."""
    consumo_por_periodo: Mapping[str, float] = field(default_factory=dict)
    """Consumo de la obra y material por periodo (`AAAA-MM`), sin la salida evaluada."""


@dataclass(frozen=True)
class VariablesSalida:
    log_cantidad: float
    z_obra_material: float
    razon_material: float
    razon_periodo: float
    hora_sin: float
    hora_cos: float
    fuera_horario: int
    domingo: int
    supera_stock: int
    log_horas_anterior: float
    salidas_dia: int
    salidas_1h: int
    """Salidas del mismo material en la obra en la última hora (incluida esta): describe las ráfagas."""
    salidas_3h: int
    """Ídem en las últimas 3 horas."""
    # Datos para escribir los motivos (no entran al modelo).
    veces_obra_material: float | None
    """Cantidad / mediana de la misma obra y material; `None` si la obra tiene menos de 5 salidas del material."""
    hora_local: str
    """`HH:MM` en la zona del negocio."""
    minutos_anterior: float | None
    """Minutos desde la salida anterior del material en la obra; `None` si no hay."""

    def vector(self) -> tuple[float, ...]:
        """Valores en el orden de `NOMBRES_VARIABLES`."""
        return tuple(float(getattr(self, nombre)) for nombre in NOMBRES_VARIABLES)

    def como_dict(self) -> dict[str, float]:
        """Valores usados por el modelo, por nombre (se guardan con el consumo atípico para auditar)."""
        return dict(zip(NOMBRES_VARIABLES, self.vector(), strict=True))


def calcular_variables_salida(
    salida: SalidaMaterial, historial: Historial, zona: str = ZONA_NEGOCIO
) -> VariablesSalida:
    cantidad = max(float(salida.cantidad), 0.0)
    obra_material = list(historial.obra_material)[-VENTANA_OBRA_MATERIAL:]
    material = list(historial.material)[-VENTANA_MATERIAL:]
    cantidades_obra = [previa.cantidad for previa in obra_material]

    # z robusto contra la obra y material; si no alcanza, contra el material en todas las obras.
    grupo = cantidades_obra if len(cantidades_obra) >= MINIMO_GRUPO else material
    z = _z_robusto(cantidad, grupo) if len(grupo) >= MINIMO_GRUPO else 0.0
    veces_obra = None
    if len(cantidades_obra) >= MINIMO_GRUPO:
        mediana_obra = median(cantidades_obra)
        veces_obra = cantidad / mediana_obra if mediana_obra > 0 else None

    mediana_material = median(material) if material else 0.0
    razon_material = cantidad / mediana_material if mediana_material > 0 else 1.0

    anteriores = [valor for periodo, valor in historial.consumo_por_periodo.items() if periodo < salida.periodo]
    consumo_actual = historial.consumo_por_periodo.get(salida.periodo, 0.0) + cantidad
    promedio = mean(anteriores) if anteriores else 0.0
    razon_periodo = min(consumo_actual / promedio, MAXIMO_RAZON_PERIODO) if promedio > 0 else 1.0

    local = salida.registrado_en.astimezone(ZoneInfo(zona))
    hora = local.hour + local.minute / 60 + local.second / 3600
    angulo = 2 * math.pi * hora / 24

    if obra_material:
        horas = (salida.registrado_en - obra_material[-1].registrado_en).total_seconds() / 3600
        horas = min(max(horas, 0.0), MAXIMO_HORAS_ANTERIOR)
        minutos_anterior: float | None = horas * 60
    else:
        horas = MAXIMO_HORAS_ANTERIOR
        minutos_anterior = None

    # El historial está en orden de fecha: se cuenta hacia atrás hasta salir del día.
    dia = local.date()
    salidas_dia = 1
    for previa in reversed(obra_material):
        if previa.registrado_en.astimezone(local.tzinfo).date() != dia:
            break
        salidas_dia += 1
    salidas_1h = 1 + _recientes(obra_material, salida.registrado_en, timedelta(hours=1))
    salidas_3h = 1 + _recientes(obra_material, salida.registrado_en, timedelta(hours=3))

    return VariablesSalida(
        log_cantidad=math.log1p(cantidad),
        z_obra_material=z,
        razon_material=razon_material,
        razon_periodo=razon_periodo,
        hora_sin=math.sin(angulo),
        hora_cos=math.cos(angulo),
        fuera_horario=int(local.hour < HORA_INICIO_HABITUAL or local.hour >= HORA_FIN_HABITUAL),
        domingo=int(local.weekday() == DOMINGO),
        supera_stock=int(salida.supera_stock),
        log_horas_anterior=math.log1p(horas),
        salidas_dia=salidas_dia,
        salidas_1h=salidas_1h,
        salidas_3h=salidas_3h,
        veces_obra_material=veces_obra,
        hora_local=f"{local.hour:02d}:{local.minute:02d}",
        minutos_anterior=minutos_anterior,
    )


def _recientes(obra_material: Sequence[SalidaPrevia], momento: datetime, ventana: timedelta) -> int:
    """Salidas anteriores dentro de `ventana` antes de `momento` (el historial está en orden de fecha)."""
    cantidad = 0
    for previa in reversed(obra_material):
        if momento - previa.registrado_en > ventana:
            break
        cantidad += 1
    return cantidad


def _z_robusto(cantidad: float, grupo: Sequence[float]) -> float:
    centro = median(grupo)
    mad = median(abs(valor - centro) for valor in grupo)
    if mad == 0:
        if cantidad == centro:
            return 0.0
        return math.copysign(LIMITE_Z, cantidad - centro)
    z = (cantidad - centro) / (ESCALA_MAD * mad)
    return max(-LIMITE_Z, min(LIMITE_Z, z))
