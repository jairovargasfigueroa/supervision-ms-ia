"""Resultado de un reporte (filas como texto), lo aplicado en texto para la persona, totales y plantilla del resumen."""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from ia.constructor_reportes.dominio.catalogo import MONTO, NUMERO
from ia.constructor_reportes.dominio.receta import LIMITE_REST
from ia.constructor_reportes.dominio.validacion import Columna, FiltroValidado, RecetaValidada, ValorFiltro

AVISO_MATERIALES = "Nombres de materiales no disponibles"
AVISO_CLIENTES = "Nombres de clientes no disponibles"
AVISO_PLANILLAS_SIN_PERIODO = "Las planillas sin periodo no se incluyen al filtrar por periodo"

Fila = list[str | None]


@dataclass(frozen=True)
class ElementoAplicado:
    nombre: str
    valor: str


@dataclass
class Resultado:
    columnas: list[Columna]
    filas: list[Fila]
    truncado: bool
    avisos: list[str] = field(default_factory=list)

    @property
    def total_filas(self) -> int:
        return len(self.filas)


def describir_aplicado(receta: RecetaValidada) -> list[ElementoAplicado]:
    """Filtros, agrupación, métricas, orden y límite (si no es 100), en texto para que la persona compruebe."""
    conjunto = receta.catalogo
    aplicado: list[ElementoAplicado] = []
    vistos: set[str] = set()
    for f in receta.filtros:
        if f.campo in vistos:
            continue
        vistos.add(f.campo)
        definicion = conjunto.filtro(f.campo, receta.modo)
        titulo = definicion.titulo if definicion else f.campo
        aplicado.append(ElementoAplicado(titulo, _describir_filtro(receta.filtros_de(f.campo))))
    titulos = {c.clave: c.titulo for c in receta.columnas_salida()}
    if receta.agrupada:
        aplicado.append(ElementoAplicado("Agrupado por", ", ".join(titulos[c] for c in receta.agrupar_por)))
        aplicado.append(ElementoAplicado("Métricas", ", ".join(titulos[m.clave] for m in receta.metricas)))
    if receta.orden:
        aplicado.append(
            ElementoAplicado(
                "Orden",
                ", ".join(
                    f"{titulos.get(por, por)} ({'mayor a menor' if d == 'DESC' else 'menor a mayor'})"
                    for por, d in receta.orden
                ),
            )
        )
    if receta.limite != LIMITE_REST:
        aplicado.append(ElementoAplicado("Límite", str(receta.limite)))
    return aplicado


def _describir_filtro(filtros: list[FiltroValidado]) -> str:
    por_operador = {f.operador: f.valor for f in filtros}
    if "DESDE" in por_operador and "HASTA" in por_operador:
        return f"{_texto(por_operador['DESDE'])} a {_texto(por_operador['HASTA'])}"
    if "DESDE" in por_operador:
        return f"desde {_texto(por_operador['DESDE'])}"
    if "HASTA" in por_operador:
        return f"hasta {_texto(por_operador['HASTA'])}"
    if "CONTIENE" in por_operador:
        return f"contiene «{_texto(por_operador['CONTIENE'])}»"
    filtro = filtros[0]
    if filtro.campo == "venceEnDias":
        return f"próximos {filtro.valor} días"
    return _texto(filtro.valor)


def _texto(valor: ValorFiltro) -> str:
    if valor is True:
        return "Sí"
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, tuple):
        return ", ".join(valor)
    return str(valor)


def totales(resultado: Resultado) -> dict[str, str]:
    """Lo que calcula el código para el resumen: número de filas y la suma de cada columna `MONTO` o `NUMERO`."""
    datos = {"filas": str(resultado.total_filas)}
    for i, columna in enumerate(resultado.columnas):
        if columna.tipo not in (MONTO, NUMERO):
            continue
        suma = Decimal(0)
        for fila in resultado.filas:
            valor = fila[i]
            if valor is None:
                continue
            try:
                suma += Decimal(valor)
            except InvalidOperation:
                continue
        datos[f"suma de {columna.titulo}"] = format(suma.normalize() if suma == suma.to_integral() else suma, "f")
    return datos


def resumen_plantilla(titulo: str, aplicado: list[ElementoAplicado], total_filas: int) -> str:
    """Respaldo cuando el modelo falla o tarda (503, criterio 10)."""
    no_filtros = ("Agrupado por", "Métricas", "Orden", "Límite")
    filtros = "; ".join(f"{e.nombre}: {e.valor}" for e in aplicado if e.nombre not in no_filtros)
    filtros = filtros or "ninguno"
    if total_filas == 0:
        return f"No hay datos para «{titulo}» con los filtros: {filtros}."
    return f"Se encontraron {total_filas} filas para «{titulo}» con los filtros: {filtros}."
