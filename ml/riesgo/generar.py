"""Generador de obras sintéticas para el modelo de riesgo de retraso (402).

Simula obras completas mes a mes con las reglas de `parametros.yaml` (diseño de la 402, «Datos sintéticos») y, en
3 días al azar de cada obra, arma el `EstadoObra` con lo ocurrido hasta ese día y llama a `calcular_variables` (la
misma función que usa el servicio). Reproducible: misma semilla, mismo CSV.

    uv run python -m ml.riesgo.generar --obras 4000 --semilla 2026
"""

import argparse
import calendar
import csv
import io
import math
import sys
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from ia.reportes.dominio.indicadores import plazo_pct
from ia.riesgo.dominio.variables import VARIABLES, EstadoObra, PlanillaAprobadaObra, calcular_variables

CARPETA = Path(__file__).resolve().parent
PARAMETROS = CARPETA / "parametros.yaml"
SALIDA = CARPETA / "datos" / "riesgo_sintetico.csv"
COLUMNAS = ("obra_id", "fecha_corte", *VARIABLES, "se_retrasa")
DIAS_POR_MES = 30.44


@dataclass
class ObraSimulada:
    obra_id: str
    fecha_inicio: date
    plazo_dias: int
    monto: float
    dias_reales: float
    se_retrasa: bool
    planillas: list[tuple[date, str, float]] = field(default_factory=list)
    """(fecha de aprobación, periodo, avance del periodo en puntos)."""
    observaciones: list[date] = field(default_factory=list)
    atipicos: list[tuple[date, int]] = field(default_factory=list)
    sin_stock: list[tuple[date, int]] = field(default_factory=list)


def leer_parametros(ruta: Path = PARAMETROS) -> dict[str, Any]:
    with ruta.open(encoding="utf-8") as archivo:
        datos: dict[str, Any] = yaml.safe_load(archivo)
    return datos


def _mes(inicio: date, desplazamiento: int) -> tuple[int, int]:
    total = inicio.year * 12 + inicio.month - 1 + desplazamiento
    return total // 12, total % 12 + 1


def _ultimo_dia(anio: int, mes: int) -> date:
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


def simular_obra(numero: int, p: dict[str, Any], rng: np.random.Generator) -> ObraSimulada:
    plazo = int(rng.integers(p["plazo_dias"][0], p["plazo_dias"][1] + 1))
    monto = float(math.exp(rng.uniform(math.log(p["monto"][0]), math.log(p["monto"][1]))))
    desde = date.fromisoformat(p["inicio"][0] + "-01")
    hasta = date.fromisoformat(p["inicio"][1] + "-01")
    meses_posibles = (hasta.year - desde.year) * 12 + hasta.month - desde.month
    anio, mes = _mes(desde, int(rng.integers(0, meses_posibles + 1)))
    inicio = date(anio, mes, 1)

    ef = p["eficiencia"]
    e = float(np.clip(rng.normal(ef["media"], ef["desviacion"]), ef["minimo"], ef["maximo"]))
    pm = float(rng.beta(p["material"]["a"], p["material"]["b"]))
    q = float(rng.beta(p["observaciones"]["a"], p["observaciones"]["b"]))

    meses_plazo = plazo / DIAS_POR_MES
    k = p["curva_s"]["pendiente"]

    def logistica(t: float) -> float:
        return 1 / (1 + math.exp(-k * (t / meses_plazo - 0.5)))

    base0, base1 = logistica(0), logistica(meses_plazo)

    def planificado(t: float) -> float:
        return (logistica(min(t, meses_plazo)) - base0) / (base1 - base0)

    ritmo_post = p["ritmo_despues_del_plazo"] / meses_plazo
    lluvia = p["lluvia"]
    obra = ObraSimulada(f"sint-{numero:05d}", inicio, plazo, monto, 0.0, False)
    acumulado = 0.0
    pendiente = 0.0  # avance hecho que todavía no entró en una planilla aprobada
    observada_antes = False
    i = 0
    while acumulado < 100 and i < 600:
        anio, mes = _mes(inicio, i)
        plan = planificado(i + 1) - planificado(i) if i < meses_plazo else ritmo_post
        if i < meses_plazo < i + 1:  # el último mes del plazo: la parte que sobra va al ritmo posterior
            plan += ritmo_post * (i + 1 - meses_plazo)
        prob_lluvia = lluvia["probabilidad_lluviosos"] if mes in lluvia["meses"] else lluvia["probabilidad_resto"]
        choque = float(rng.uniform(*lluvia["reduccion"])) if rng.random() < prob_lluvia else 0.0
        real = 100 * plan * e * (1 - choque) * (1 - p["impacto_material"] * pm)
        medio_mes = date(anio, mes, 15)
        obra.atipicos.append((medio_mes, int(rng.poisson(p["atipicos_por_mes"] * pm))))
        obra.sin_stock.append((medio_mes, int(rng.poisson(p["sin_stock_por_mes"] * pm))))
        if acumulado + real >= 100:
            fraccion = (100 - acumulado) / real if real > 0 else 0.0
            obra.dias_reales = (i + fraccion) * DIAS_POR_MES
            real = 100 - acumulado
        acumulado += real
        pendiente += real
        fin_de_mes = _ultimo_dia(anio, mes)
        if pendiente > 0:
            # Con probabilidad q la planilla se observa primero y su avance se aprueba el mes siguiente (junto con el
            # de ese mes); la que viene de una observación ya no se vuelve a observar.
            if not observada_antes and rng.random() < q:
                obra.observaciones.append(fin_de_mes)
                observada_antes = True
            else:
                observada_antes = False
                obra.planillas.append((fin_de_mes, f"{anio:04d}-{mes:02d}", pendiente))
                pendiente = 0.0
        i += 1
    if pendiente > 0:  # la última observada se aprueba el mes siguiente
        anio, mes = _mes(inicio, i)
        obra.planillas.append((_ultimo_dia(anio, mes), f"{anio:04d}-{mes:02d}", pendiente))
    obra.se_retrasa = obra.dias_reales > plazo * (1 + p["retraso_tolerado"])
    return obra


def estado_en(obra: ObraSimulada, hoy: date) -> EstadoObra:
    """Lo que la plataforma sabría ese día: planillas aprobadas hasta hoy y eventos de los últimos 90 días."""
    aprobadas = tuple(
        PlanillaAprobadaObra(periodo, round(avance, 2)) for fecha, periodo, avance in obra.planillas if fecha <= hoy
    )
    desde = hoy - timedelta(days=90)
    return EstadoObra(
        fecha_inicio=obra.fecha_inicio,
        plazo_dias=obra.plazo_dias,
        monto_contrato=round(obra.monto, 2),
        avance_pct=min(round(sum(p.avance_periodo_pct for p in aprobadas), 2), 100.0),
        plazo_pct=float(plazo_pct(obra.fecha_inicio, obra.plazo_dias, hoy)),
        planillas_aprobadas=aprobadas,
        planillas_observadas=sum(1 for fecha in obra.observaciones if fecha <= hoy),
        atipicos_90d=sum(n for fecha, n in obra.atipicos if desde < fecha <= hoy),
        supera_stock_90d=sum(n for fecha, n in obra.sin_stock if desde < fecha <= hoy),
    )


def generar(parametros: dict[str, Any], obras: int, semilla: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rng = np.random.default_rng(semilla)
    filas: list[dict[str, Any]] = []
    retrasadas = 0
    for numero in range(1, obras + 1):
        obra = simular_obra(numero, parametros, rng)
        retrasadas += obra.se_retrasa
        # Días de corte con plazo transcurrido entre corte_minimo y corte_maximo (redondeados hacia adentro).
        primero = math.ceil(parametros["corte_minimo"] * obra.plazo_dias)
        ultimo = math.floor(parametros["corte_maximo"] * obra.plazo_dias)
        for dias in sorted(rng.integers(primero, ultimo + 1, parametros["cortes_por_obra"])):
            hoy = obra.fecha_inicio + timedelta(days=int(dias))
            variables = calcular_variables(estado_en(obra, hoy), hoy)
            filas.append(
                {
                    "obra_id": obra.obra_id,
                    "fecha_corte": hoy.isoformat(),
                    **{nombre: round(valor, 4) for nombre, valor in variables.items()},
                    "se_retrasa": int(obra.se_retrasa),
                }
            )
    resumen = {
        "obras": obras,
        "filas": len(filas),
        "semilla": semilla,
        "obras_retrasadas": retrasadas,
        "proporcion_retrasadas": round(retrasadas / obras, 4) if obras else 0.0,
    }
    return filas, resumen


def a_csv(filas: list[dict[str, Any]]) -> str:
    salida = io.StringIO()
    escritor = csv.DictWriter(salida, fieldnames=COLUMNAS, lineterminator="\n")
    escritor.writeheader()
    escritor.writerows(filas)
    return salida.getvalue()


def main(argumentos: list[str] | None = None) -> int:
    lector = argparse.ArgumentParser(description="Genera obras sintéticas para el modelo de riesgo de retraso (402).")
    lector.add_argument(
        "--obras", type=int, default=None, help="cantidad de obras (por defecto, la de parametros.yaml)"
    )
    lector.add_argument("--semilla", type=int, default=None, help="semilla (por defecto, la de parametros.yaml)")
    lector.add_argument("--parametros", type=Path, default=PARAMETROS)
    lector.add_argument("--salida", type=Path, default=SALIDA)
    args = lector.parse_args(argumentos)

    inicio = time.perf_counter()
    parametros = leer_parametros(args.parametros)
    obras = args.obras or int(parametros["obras"])
    semilla = args.semilla if args.semilla is not None else int(parametros["semilla"])
    filas, resumen = generar(parametros, obras, semilla)
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(a_csv(filas), encoding="utf-8")
    segundos = time.perf_counter() - inicio
    minimo, maximo = parametros["proporcion_retrasadas"]
    print(
        f"{resumen['obras']} obras, {resumen['filas']} filas, retrasadas {resumen['proporcion_retrasadas']:.1%} "
        f"(objetivo {minimo:.0%} a {maximo:.0%}) en {segundos:.1f} s: {args.salida}"
    )
    if not minimo <= resumen["proporcion_retrasadas"] <= maximo:
        print("La proporción de obras retrasadas está fuera del objetivo: ajusta parametros.yaml.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
