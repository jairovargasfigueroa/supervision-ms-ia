"""Cruce del paso compuesto `consumo_vs_previsto` de Control de materiales (505).

Con el `avance_obra {periodo}` (lote: partidas con `materialPrevisto`) y el `consumos_partida {periodo}` (lote: filas de
todas las obras), el código cruza obra + partida + material y al modelo le llega solo lo que importa:

- `excesos`: partidas donde lo usado supera en más de 20 % a lo previsto (usado > 1,20 × previsto), de mayor a menor
  `porcentaje` (usado / previsto × 100, con un decimal);
- `salidasSinStock`: filas con salidas que superaron el stock, de más a menos;

hasta 30 filas de cada uno, con `truncado` si se recortó algo (aquí o en las herramientas). Sin el avance (la
herramienta falló) no hay `excesos`: solo `salidasSinStock`.
"""

from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any, Final

EXCESO: Final = Decimal("1.20")
FILAS_MAXIMAS: Final = 30


def cruzar(avance: Any, consumos: Any, periodo: str) -> dict[str, Any]:
    filas = list(_objetos(consumos, "filas"))
    excesos: list[dict[str, Any]] = []
    sin_stock: list[dict[str, Any]] = []
    previstos = _previstos(avance) if avance is not None else None
    for fila in filas:
        obra_id = _texto(fila.get("obraId"))
        partida_id = _texto(fila.get("partidaId"))
        material_id = _texto(fila.get("materialId"))
        material = _texto(fila.get("nombre"))
        if previstos is not None:
            previsto = previstos.get((obra_id.lower(), partida_id.lower(), material_id.lower()))
            usado = _numero(fila.get("usado"))
            if previsto is not None and usado is not None and usado > previsto * EXCESO:
                excesos.append(
                    {
                        "obraId": obra_id,
                        "obraNombre": _texto(fila.get("obraNombre")),
                        "partidaId": partida_id,
                        "materialId": material_id,
                        "material": material,
                        "previsto": float(previsto),
                        "usado": float(usado),
                        "porcentaje": float(round(usado / previsto * 100, 1)),
                    }
                )
        salidas = fila.get("salidasSuperanStock")
        if isinstance(salidas, int) and not isinstance(salidas, bool) and salidas > 0:
            sin_stock.append(
                {
                    "obraId": obra_id,
                    "obraNombre": _texto(fila.get("obraNombre")),
                    "partidaId": partida_id,
                    "material": material,
                    "salidasSuperanStock": salidas,
                }
            )
    excesos.sort(key=lambda e: -e["porcentaje"])
    sin_stock.sort(key=lambda s: -s["salidasSuperanStock"])
    truncado = (
        len(excesos) > FILAS_MAXIMAS
        or len(sin_stock) > FILAS_MAXIMAS
        or _truncado(consumos)
        or (avance is not None and _truncado(avance))
    )
    salida: dict[str, Any] = {"periodo": periodo}
    if previstos is not None:
        salida["excesos"] = excesos[:FILAS_MAXIMAS]
    salida["salidasSinStock"] = sin_stock[:FILAS_MAXIMAS]
    salida["truncado"] = truncado
    return salida


def _previstos(avance: Any) -> dict[tuple[str, str, str], Decimal]:
    """(obra, partida, material) → cantidad prevista del periodo (mayor que cero)."""
    previstos: dict[tuple[str, str, str], Decimal] = {}
    for obra in _objetos(avance, "obras"):
        obra_id = _texto(obra.get("obraId")).lower()
        for partida in _objetos(obra, "partidas"):
            partida_id = _texto(partida.get("partidaId")).lower()
            for material in _objetos(partida, "materialPrevisto"):
                cantidad = _numero(material.get("cantidadPrevista"))
                if cantidad is not None and cantidad > 0:
                    clave = (obra_id, partida_id, _texto(material.get("materialId")).lower())
                    previstos[clave] = previstos.get(clave, Decimal(0)) + cantidad
    return previstos


def _objetos(datos: Any, clave: str) -> Iterable[dict[str, Any]]:
    lista = datos.get(clave) if isinstance(datos, dict) else None
    return [e for e in lista if isinstance(e, dict)] if isinstance(lista, list) else []


def _texto(valor: Any) -> str:
    return valor if isinstance(valor, str) else ""


def _numero(valor: Any) -> Decimal | None:
    if isinstance(valor, bool) or not isinstance(valor, int | float | str):
        return None
    try:
        numero = Decimal(str(valor))
    except InvalidOperation:
        return None
    return numero if numero.is_finite() else None


def _truncado(datos: Any) -> bool:
    return isinstance(datos, dict) and datos.get("truncado") is True
