"""Expresión de SQLAlchemy Core de cada columna, dimensión, medida y filtro del catálogo (constantes).

Los nombres de tablas y columnas salen **solo** de aquí (tablas de la 401); lo que viene del usuario o del modelo entra
en `constructor_sql.py` únicamente como valor enlazado. `hoy` se enlaza en cada consulta (`bindparam`).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Date, Integer, bindparam, case, func, literal
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import FromClause

from ia.comun.tiempo import ZONA_NEGOCIO
from ia.reportes.infraestructura.tablas import (
    rpt_cobro,
    rpt_consumo_material,
    rpt_movimiento,
    rpt_obra_resumen,
    rpt_planilla,
)

o = rpt_obra_resumen.c
cm = rpt_consumo_material.c
m = rpt_movimiento.c
c = rpt_cobro.c
p = rpt_planilla.c

HOY = bindparam("hoy", type_=Date)
"""Fecha de hoy en La Paz: la enlaza el constructor en cada consulta."""
ZONA = literal(ZONA_NEGOCIO)

UMBRAL_EN_RIESGO = 10
"""`plazo_pct − avance_pct > 10` (reglas del negocio, «Avance y plazo»)."""
MOTIVO_MAXIMO = 200


def fecha_en_la_paz(instante: ColumnElement[Any]) -> ColumnElement[Any]:
    return func.date(func.timezone(ZONA, instante))


@dataclass(frozen=True)
class ExpresionesConjunto:
    origen: FromClause
    campos: Mapping[str, ColumnElement[Any]]
    """Columna, dimensión o medida → expresión (`material` y `cliente` dan el id)."""
    filtros: Mapping[str, ColumnElement[Any]]
    """Filtro → expresión con la que se compara (los booleanos son ya la condición)."""
    condiciones: tuple[ColumnElement[bool], ...] = ()
    """Siempre se aplican (por ejemplo, cobros con número)."""


DIFERENCIA = o.plazo_pct - o.avance_pct
NIVEL_RIESGO = func.coalesce(o.nivel_riesgo, literal("SIN_DATOS"))

_DE_LA_OBRA: dict[str, ColumnElement[Any]] = {"obra": o.nombre, "zona": o.zona, "cliente": o.cliente_id}
_FILTROS_OBRA: dict[str, ColumnElement[Any]] = {
    "obra": o.nombre,
    "obraId": o.obra_id,
    "zona": o.zona,
    "cliente": o.cliente_id,
    "clienteId": o.cliente_id,
}

OBRAS = ExpresionesConjunto(
    origen=rpt_obra_resumen,
    campos={
        **_DE_LA_OBRA,
        "estado": o.estado,
        "nivel_riesgo": NIVEL_RIESGO,
        "fecha_inicio": o.fecha_inicio,
        "plazo_dias": o.plazo_dias,
        "monto_contrato": o.monto_contrato,
        "avance_pct": o.avance_pct,
        "plazo_pct": o.plazo_pct,
        "diferencia": DIFERENCIA,
        "en_riesgo": DIFERENCIA > UMBRAL_EN_RIESGO,
        "riesgo_pct": func.round(o.riesgo_retraso * 100, 1),
    },
    filtros={
        **_FILTROS_OBRA,
        "estado": o.estado,
        "nivelRiesgo": NIVEL_RIESGO,
        "soloEnRiesgo": DIFERENCIA > UMBRAL_EN_RIESGO,
    },
)

CONSUMO_MATERIAL = ExpresionesConjunto(
    origen=rpt_consumo_material.join(rpt_obra_resumen, cm.obra_id == o.obra_id),
    campos={
        **_DE_LA_OBRA,
        "material": cm.material_id,
        "periodo": cm.periodo,
        "cantidad": cm.cantidad,
        "monto": cm.monto,
        "salidas": cm.salidas,
    },
    filtros={**_FILTROS_OBRA, "material": cm.material_id, "materialId": cm.material_id, "periodo": cm.periodo},
)

FECHA_MOVIMIENTO = fecha_en_la_paz(m.registrado_en)
MOVIMIENTOS = ExpresionesConjunto(
    origen=rpt_movimiento.join(rpt_obra_resumen, m.obra_id == o.obra_id),
    campos={
        **_DE_LA_OBRA,
        "material": m.material_id,
        "tipo": m.tipo,
        "periodo": m.periodo,
        "supera_stock": m.supera_stock,
        "fecha": FECHA_MOVIMIENTO,
        "cantidad": m.cantidad,
    },
    filtros={
        **_FILTROS_OBRA,
        "material": m.material_id,
        "materialId": m.material_id,
        "tipo": m.tipo,
        "soloSuperaStock": m.supera_stock.is_(True),
        "periodo": m.periodo,
        "fecha": FECHA_MOVIMIENTO,
    },
)

MES_EMISION = func.to_char(c.emitido_en, literal("YYYY-MM"))
COBROS = ExpresionesConjunto(
    # Solo cobros con número y con obra: hasta que llegan CobroEmitido y PlanillaAprobada pueden faltar (401).
    origen=rpt_cobro.join(rpt_obra_resumen, c.obra_id == o.obra_id),
    campos={
        "numero": c.numero,
        "obra": o.nombre,
        "cliente": c.cliente_id,
        "zona": c.zona,
        "estado": c.estado,
        "mes_emision": MES_EMISION,
        "monto": c.monto,
        "monto_vencido": case((c.estado == literal("VENCIDO"), c.monto), else_=literal(0)),
        "emitido_en": c.emitido_en,
        "vence_en": c.vence_en,
        "dias_para_vencer": (c.vence_en - HOY).cast(Integer),
    },
    filtros={
        "obra": o.nombre,
        "obraId": c.obra_id,
        "zona": c.zona,
        "cliente": c.cliente_id,
        "clienteId": c.cliente_id,
        "estado": c.estado,
        "venceEnDias": c.vence_en,
        "periodo": MES_EMISION,
    },
    condiciones=(c.numero.is_not(None),),
)

PLANILLAS = ExpresionesConjunto(
    origen=rpt_planilla.join(rpt_obra_resumen, p.obra_id == o.obra_id),
    campos={
        **_DE_LA_OBRA,
        "periodo": p.periodo,
        "estado": p.estado,
        "neto": p.neto,
        "monto_periodo": p.monto_periodo,
        "avance_periodo_pct": p.avance_periodo_pct,
        "veces_observada": p.veces_observada,
        # Lo escribe el cliente: dato no confiable (504); se recorta.
        "ultimo_motivo": func.left(p.ultimo_motivo, MOTIVO_MAXIMO),
        "ultimo_origen": p.ultimo_origen,
        "aprobada_en": fecha_en_la_paz(p.aprobada_en),
    },
    filtros={
        **_FILTROS_OBRA,
        "estado": p.estado,
        "periodo": p.periodo,
        "aprobadaEn": fecha_en_la_paz(p.aprobada_en),
    },
)

SQL: dict[str, ExpresionesConjunto] = {
    "OBRAS": OBRAS,
    "CONSUMO_MATERIAL": CONSUMO_MATERIAL,
    "MOVIMIENTOS": MOVIMIENTOS,
    "COBROS": COBROS,
    "PLANILLAS": PLANILLAS,
}

# CONSUMOS_ATIPICOS: los filtros de obra, zona y cliente se resuelven antes en rpt_obra_resumen.
FILTROS_OBRA_ATIPICOS = _FILTROS_OBRA
