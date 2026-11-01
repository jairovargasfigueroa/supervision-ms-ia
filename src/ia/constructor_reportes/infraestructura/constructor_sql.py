"""Arma el `select()` de SQLAlchemy Core desde la receta validada.

Los nombres de tablas y columnas, los operadores, las funciones de agregación y la dirección del orden salen **solo**
del catálogo (`expresiones.py`); lo que viene del usuario o del modelo entra **solo como valor enlazado**. No se usa
`text()` ni se arma SQL con *f-strings* ni concatenación. `CONTIENE` → `ILIKE` con `\\`, `%` y `_` escapados.
Se piden `limite + 1` filas para saber si hay más (`truncado`).
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import Select, and_, func, select, true
from sqlalchemy.sql.elements import ColumnElement, Label

from ia.constructor_reportes.aplicacion.puertos import ConsultaResuelta
from ia.constructor_reportes.dominio.validacion import FiltroValidado, RecetaValidada
from ia.constructor_reportes.infraestructura.expresiones import FILTROS_OBRA_ATIPICOS, HOY, SQL, o

_AGREGACIONES: dict[str, Callable[[ColumnElement[Any] | None], ColumnElement[Any]]] = {
    "CONTEO": lambda _: func.count(),
    "SUMA": lambda e: func.sum(e),
    "PROMEDIO": lambda e: func.round(func.avg(e), 2),
    "MAXIMO": lambda e: func.max(e),
    "MINIMO": lambda e: func.min(e),
}
_BOOLEANOS = frozenset({"soloEnRiesgo", "soloSuperaStock"})
"""Filtros cuya expresión ya es la condición (valor `true`)."""
ESCAPE = "\\"


def patron_contiene(texto: str) -> str:
    """`%texto%` con `\\`, `%` y `_` escapados (el texto del usuario nunca actúa como comodín)."""
    escapado = texto.replace(ESCAPE, ESCAPE * 2).replace("%", ESCAPE + "%").replace("_", ESCAPE + "_")
    return f"%{escapado}%"


def construir_select(consulta: ConsultaResuelta) -> Select[Any]:
    receta = consulta.receta
    expresiones = SQL[receta.conjunto]
    etiquetas: dict[str, Label[Any]] = {}
    agrupar: list[ColumnElement[Any]] = []
    if receta.agrupada:
        for clave in receta.agrupar_por:
            etiquetas[clave] = expresiones.campos[clave].label(clave)
            agrupar.append(expresiones.campos[clave])
        for metrica in receta.metricas:
            origen = expresiones.campos[metrica.campo] if metrica.campo else None
            etiquetas[metrica.clave] = _AGREGACIONES[metrica.funcion](origen).label(metrica.clave)
    else:
        for clave in receta.columnas:
            etiquetas[clave] = expresiones.campos[clave].label(clave)

    consulta_sql = (
        select(*etiquetas.values())
        .select_from(expresiones.origen)
        .where(and_(true(), *expresiones.condiciones, *condiciones(consulta, expresiones.filtros)))
    )
    if agrupar:
        consulta_sql = consulta_sql.group_by(*agrupar)
    orden = [
        etiquetas[por].desc().nulls_last() if direccion == "DESC" else etiquetas[por].asc().nulls_last()
        for por, direccion in receta.orden
    ]
    return consulta_sql.order_by(*orden).limit(receta.limite + 1).params(hoy=consulta.hoy)


def condiciones(consulta: ConsultaResuelta, filtros: Mapping[str, ColumnElement[Any]]) -> list[ColumnElement[bool]]:
    return [_condicion(f, filtros[f.campo], consulta) for f in consulta.receta.filtros if f.campo in filtros]


def _condicion(f: FiltroValidado, expresion: ColumnElement[Any], consulta: ConsultaResuelta) -> ColumnElement[bool]:
    if f.campo in _BOOLEANOS:
        return expresion.is_(True)
    if f.operador == "CONTIENE":
        if f.campo == "cliente":
            return expresion.in_(list(consulta.ids_cliente or ()))
        if f.campo == "material":
            return expresion.in_(list(consulta.ids_material or ()))
        assert isinstance(f.valor, str)
        return expresion.ilike(patron_contiene(f.valor), escape=ESCAPE)
    if f.campo == "venceEnDias":
        assert isinstance(f.valor, int)
        return and_(expresion >= HOY, expresion <= consulta.hoy + timedelta(days=f.valor))
    if f.operador == "EN":
        assert isinstance(f.valor, tuple)
        return expresion.in_(list(f.valor))
    if f.operador == "DESDE":
        return expresion >= f.valor
    if f.operador == "HASTA":
        return expresion <= f.valor
    return expresion == f.valor


def seleccion_obras_filtradas(consulta: ConsultaResuelta) -> Select[Any] | None:
    """Para CONSUMOS_ATIPICOS: `obra_id` de las obras que cumplen los filtros de obra, zona y cliente (o `None` si no
    hay filtros de obra)."""
    receta: RecetaValidada = consulta.receta
    if not any(f.campo in FILTROS_OBRA_ATIPICOS for f in receta.filtros):
        return None
    return select(o.obra_id).where(and_(true(), *condiciones(consulta, FILTROS_OBRA_ATIPICOS))).params(hoy=consulta.hoy)


def seleccion_nombres_obras(obra_ids: Sequence[str]) -> Select[Any]:
    return select(o.obra_id, o.nombre).where(o.obra_id.in_(list(obra_ids)))


def seleccion_obras_por_nombre() -> Select[Any]:
    """Todas las obras por nombre (para ordenar por obra en MongoDB con una lista constante de ids)."""
    return select(o.obra_id, o.nombre).order_by(o.nombre.asc(), o.obra_id.asc())
