"""Genera las salidas sintéticas de la 403 (diseño, «Datos sintéticos»): normales más un 2 % de anomalías inyectadas.

    uv run python -m ml.atipicos.generar --semilla 2026

Las variables de cada salida se calculan con `calcular_variables_salida`, la misma función que usa el worker, con el
historial anterior en memoria (`recorrer_en_orden`). Escribe `ml/atipicos/datos/salidas_sinteticas.csv` (no se
versiona) con la marca `anomalia`, que solo sirve para evaluar: el modelo no la usa.
"""

import argparse
import csv
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import yaml

from ia.atipicos.dominio.historial import recorrer_en_orden
from ia.atipicos.dominio.variables import NOMBRES_VARIABLES, SalidaMaterial

CARPETA = Path(__file__).resolve().parent
PARAMETROS = CARPETA / "parametros.yaml"
SALIDA_CSV = CARPETA / "datos" / "salidas_sinteticas.csv"

CANTIDAD = "CANTIDAD"
HORARIO = "HORARIO"
RAFAGA = "RAFAGA"
STOCK = "STOCK"
TIPOS_ANOMALIA = (CANTIDAD, HORARIO, RAFAGA, STOCK)

COLUMNAS = (
    "movimiento_id",
    "obra_id",
    "material_id",
    "registrado_en",
    "periodo",
    "cantidad",
    "anomalia",
    "tipo_anomalia",
    *NOMBRES_VARIABLES,  # incluye supera_stock (0 o 1)
)


@dataclass(frozen=True)
class SalidaSintetica:
    salida: SalidaMaterial
    tipo_anomalia: str | None = None

    @property
    def anomalia(self) -> int:
        return int(self.tipo_anomalia is not None)


def leer_parametros(ruta: Path = PARAMETROS) -> dict[str, Any]:
    with ruta.open(encoding="utf-8") as archivo:
        datos = yaml.safe_load(archivo)
    if not isinstance(datos, dict):
        raise ValueError(f"{ruta} no tiene un objeto YAML.")
    return datos


def generar_salidas(parametros: dict[str, Any], semilla: int) -> list[SalidaSintetica]:
    """Salidas normales y anomalías inyectadas, sin las variables (en orden de creación)."""
    rng = np.random.default_rng(semilla)
    zona = ZoneInfo(str(parametros["zona_horaria"]))
    anomalias: dict[str, Any] = parametros["anomalias"]

    n_materiales = int(parametros["materiales"])
    mediana_min, mediana_max = parametros["mediana_material"]
    medianas = np.exp(rng.uniform(math.log(mediana_min), math.log(mediana_max), size=n_materiales))
    materiales = [f"mat-{indice + 1:02d}" for indice in range(n_materiales)]

    contador = _Contador()
    normales: list[SalidaSintetica] = []
    medianas_obra: dict[tuple[str, str], float] = {}
    anio, mes = (int(parte) for parte in str(parametros["primer_mes_inicio"]).split("-"))
    meses_obra = int(parametros["meses_por_obra"])
    for indice_obra in range(int(parametros["obras"])):
        obra_id = f"obra-{indice_obra + 1:02d}"
        inicio = _sumar_meses(date(anio, mes, 1), int(rng.integers(0, int(parametros["meses_inicio_posibles"]))))
        factor = rng.uniform(*parametros["factor_obra"])
        elegidos = rng.choice(n_materiales, size=int(parametros["materiales_por_obra"]), replace=False)
        for indice_material in elegidos:
            meses_activo = int(
                rng.integers(parametros["meses_activo_material"][0], parametros["meses_activo_material"][1] + 1)
            )
            meses_activo = min(meses_activo, meses_obra)
            primer_mes = int(rng.integers(0, meses_obra - meses_activo + 1))
            desde = _sumar_meses(inicio, primer_mes)
            hasta = _sumar_meses(inicio, primer_mes + meses_activo)  # sin incluir
            mediana_obra = float(medianas[indice_material]) * factor
            medianas_obra[(obra_id, materiales[indice_material])] = mediana_obra
            normales.extend(
                _salidas_normales(
                    rng, parametros, zona, contador, obra_id, materiales[indice_material], mediana_obra, desde, hasta
                )
            )

    # Anomalías: 4 tipos en partes iguales. Las de ráfaga son salidas nuevas; las otras modifican salidas normales.
    proporcion = float(anomalias["proporcion"])
    por_tipo = max(1, round(proporcion * len(normales) / (len(TIPOS_ANOMALIA) - proporcion)))
    elegidas = rng.choice(len(normales), size=3 * por_tipo, replace=False)
    salidas = list(normales)
    for posicion, indice in enumerate(elegidas):
        tipo = (CANTIDAD, HORARIO, STOCK)[posicion % 3]
        salidas[indice] = _inyectar(rng, anomalias, zona, normales[indice], tipo)
    rafagas: list[SalidaSintetica] = []
    while len(rafagas) < por_tipo:
        ancla = normales[int(rng.integers(0, len(normales)))].salida
        mediana = medianas_obra[(ancla.obra_id, ancla.material_id)]
        rafagas.extend(_rafaga(rng, parametros, anomalias, zona, contador, ancla, mediana))
    return salidas + rafagas


def calcular_filas(salidas: Sequence[SalidaSintetica]) -> list[dict[str, Any]]:
    """Variables de cada salida con su historial anterior, en orden de fecha (como llegarían al worker)."""
    tipo_por_id = {s.salida.movimiento_id: s.tipo_anomalia for s in salidas}
    filas: list[dict[str, Any]] = []
    for salida, variables in recorrer_en_orden(s.salida for s in salidas):
        tipo = tipo_por_id[salida.movimiento_id]
        filas.append(
            {
                "movimiento_id": salida.movimiento_id,
                "obra_id": salida.obra_id,
                "material_id": salida.material_id,
                "registrado_en": salida.registrado_en.isoformat().replace("+00:00", "Z"),
                "periodo": salida.periodo,
                "cantidad": round(salida.cantidad, 3),
                "anomalia": int(tipo is not None),
                "tipo_anomalia": tipo or "",
                **variables.como_dict(),
            }
        )
    return filas


def escribir_csv(filas: Sequence[dict[str, Any]], ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", newline="", encoding="utf-8") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS)
        escritor.writeheader()
        escritor.writerows(filas)


def main(argumentos: Sequence[str] | None = None) -> int:
    lector = argparse.ArgumentParser(description="Genera las salidas sintéticas de consumos de material (403).")
    lector.add_argument("--semilla", type=int, default=None, help="por defecto, la de parametros.yaml (2026)")
    lector.add_argument("--parametros", type=Path, default=PARAMETROS)
    lector.add_argument("--salida", type=Path, default=SALIDA_CSV)
    opciones = lector.parse_args(argumentos)

    inicio = time.perf_counter()
    parametros = leer_parametros(opciones.parametros)
    semilla = opciones.semilla if opciones.semilla is not None else int(parametros["semilla"])
    filas = calcular_filas(generar_salidas(parametros, semilla))
    escribir_csv(filas, opciones.salida)
    anomalas = sum(fila["anomalia"] for fila in filas)
    print(
        f"{len(filas)} salidas ({anomalas} anomalías, {anomalas / len(filas):.2%}) en {opciones.salida} "
        f"· semilla {semilla} · {time.perf_counter() - inicio:.1f} s"
    )
    return 0


# Partes del generador.


class _Contador:
    def __init__(self) -> None:
        self.valor = 0

    def siguiente(self) -> str:
        self.valor += 1
        return f"s{self.valor:06d}"


def _salidas_normales(
    rng: np.random.Generator,
    parametros: dict[str, Any],
    zona: ZoneInfo,
    contador: _Contador,
    obra_id: str,
    material_id: str,
    mediana_obra: float,
    desde: date,
    hasta: date,
) -> list[SalidaSintetica]:
    minimo, maximo = parametros["salidas_por_semana"]
    sigma = float(parametros["sigma_cantidad"])
    salidas: list[SalidaSintetica] = []
    lunes = desde - timedelta(days=desde.weekday())
    while lunes < hasta:
        # Lunes a sábado de esta semana que caen dentro de los meses activos.
        dias = [lunes + timedelta(days=d) for d in range(6) if desde <= lunes + timedelta(days=d) < hasta]
        if dias:
            # En una semana incompleta (borde de los meses activos), la cantidad de salidas es proporcional a sus días.
            por_semana = int(rng.integers(minimo, maximo + 1))
            for _ in range(round(por_semana * len(dias) / 6)):
                dia = dias[int(rng.integers(0, len(dias)))]
                salidas.append(
                    SalidaSintetica(
                        _salida(
                            contador.siguiente(),
                            obra_id,
                            material_id,
                            float(rng.lognormal(math.log(mediana_obra), sigma)),
                            _hora_habitual(rng, parametros, zona, dia),
                            bool(rng.random() < float(parametros["prob_supera_stock"])),
                            zona,
                        )
                    )
                )
        lunes += timedelta(days=7)
    return salidas


def _hora_habitual(rng: np.random.Generator, parametros: dict[str, Any], zona: ZoneInfo, dia: date) -> datetime:
    hora = float(
        np.clip(
            rng.normal(parametros["hora_media"], parametros["hora_desvio"]),
            parametros["hora_minima"],
            parametros["hora_maxima"],
        )
    )
    return _instante(zona, dia, hora)


def _inyectar(
    rng: np.random.Generator, anomalias: dict[str, Any], zona: ZoneInfo, normal: SalidaSintetica, tipo: str
) -> SalidaSintetica:
    salida = normal.salida
    if tipo == CANTIDAD:
        salida = replace(salida, cantidad=salida.cantidad * float(rng.uniform(*anomalias["cantidad_factor"])))
    elif tipo == STOCK:
        salida = replace(
            salida, cantidad=salida.cantidad * float(rng.uniform(*anomalias["stock_factor"])), supera_stock=True
        )
    else:
        local = salida.registrado_en.astimezone(zona)
        if rng.random() < 0.5:
            hora = float(anomalias["noche_desde"]) + float(rng.uniform(0, float(anomalias["noche_horas"])))
            momento = _instante(zona, local.date(), hora)
        else:
            domingo = local.date() + timedelta(days=6 - local.weekday())
            momento = _instante(zona, domingo, local.hour + local.minute / 60)
        salida = _con_instante(salida, momento, zona)
    return SalidaSintetica(salida, tipo)


def _rafaga(
    rng: np.random.Generator,
    parametros: dict[str, Any],
    anomalias: dict[str, Any],
    zona: ZoneInfo,
    contador: _Contador,
    ancla: SalidaMaterial,
    mediana_obra: float,
) -> list[SalidaSintetica]:
    """De 3 a 6 salidas nuevas (cantidad habitual) del material de `ancla` en la obra, dentro de una hora del mismo
    día, en horario habitual."""
    minimo, maximo = anomalias["rafaga_salidas"]
    cantidad = int(rng.integers(minimo, maximo + 1))
    local = ancla.registrado_en.astimezone(zona)
    comienzo = _hora_habitual(rng, parametros, zona, local.date())
    minutos = np.sort(rng.uniform(0, float(anomalias["rafaga_minutos"]), size=cantidad))
    sigma = float(parametros["sigma_cantidad"])
    return [
        SalidaSintetica(
            _salida(
                contador.siguiente(),
                ancla.obra_id,
                ancla.material_id,
                float(rng.lognormal(math.log(mediana_obra), sigma)),
                comienzo + timedelta(minutes=float(minuto)),
                False,
                zona,
            ),
            RAFAGA,
        )
        for minuto in minutos
    ]


def _salida(
    movimiento_id: str,
    obra_id: str,
    material_id: str,
    cantidad: float,
    momento: datetime,
    supera_stock: bool,
    zona: ZoneInfo,
) -> SalidaMaterial:
    return _con_instante(
        SalidaMaterial(movimiento_id, obra_id, material_id, cantidad, "", supera_stock, momento), momento, zona
    )


def _con_instante(salida: SalidaMaterial, momento: datetime, zona: ZoneInfo) -> SalidaMaterial:
    """Fija el instante (redondeado al segundo, en UTC) y su periodo en la zona del negocio."""
    utc = momento.astimezone(UTC).replace(microsecond=0)
    return replace(salida, registrado_en=utc, periodo=utc.astimezone(zona).strftime("%Y-%m"))


def _instante(zona: ZoneInfo, dia: date, hora: float) -> datetime:
    """Hora local (puede pasar de 24: sigue al día siguiente) → instante con zona."""
    return datetime(dia.year, dia.month, dia.day, tzinfo=zona) + timedelta(hours=hora)


def _sumar_meses(dia: date, meses: int) -> date:
    total = dia.year * 12 + dia.month - 1 + meses
    return date(total // 12, total % 12 + 1, 1)


if __name__ == "__main__":
    sys.exit(main())
