"""`MotorReportes`: un solo motor para las personas (REST, 503) y para los agentes (MCP, 501).

Recibe una `RecetaValidada` (nunca el JSON del modelo), convierte los nombres de materiales y clientes en ids (solo en
el modo REST), ejecuta la consulta parametrizada y devuelve las filas como texto con los nombres en lugar de los ids.
"""

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ia.constructor_reportes.aplicacion.puertos import (
    CatalogoMateriales,
    ConsultaResuelta,
    EjecutorConsultas,
    MaterialCatalogo,
    MaterialesNoDisponibles,
    ServicioNoDisponible,
)
from ia.constructor_reportes.dominio.catalogo import REST
from ia.constructor_reportes.dominio.resultado import (
    AVISO_CLIENTES,
    AVISO_MATERIALES,
    AVISO_PLANILLAS_SIN_PERIODO,
    Fila,
    Resultado,
    valor_texto,
)
from ia.constructor_reportes.dominio.validacion import RecetaValidada


@dataclass(frozen=True)
class Directorio:
    """`directorio.clientes` que agrega el gateway (usuarios con rol `cliente`, de Keycloak)."""

    clientes: Mapping[str, str] = field(default_factory=dict)
    """id → nombre."""


def normalizar(texto: str) -> str:
    """Sin mayúsculas ni tildes."""
    descompuesto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in descompuesto if not unicodedata.combining(c)).casefold().strip()


class MotorReportes:
    def __init__(self, ejecutor: EjecutorConsultas, materiales: CatalogoMateriales | None = None) -> None:
        self._ejecutor = ejecutor
        self._materiales = materiales

    async def ejecutar(
        self,
        receta: RecetaValidada,
        hoy: date,
        *,
        directorio: Directorio | None = None,
        token: str | None = None,
    ) -> Resultado:
        """`ServicioNoDisponible` si falta Materiales o el directorio para un filtro; `ReportesNoDisponibles` si la
        base no responde."""
        avisos: list[str] = []
        rest = receta.modo == REST
        claves = receta.claves_salida()

        catalogo: Mapping[str, MaterialCatalogo] | None = None
        filtro_material = [f for f in receta.filtros if f.campo == "material" and f.operador == "CONTIENE"]
        if rest and (filtro_material or "material" in claves):
            try:
                if self._materiales is None or token is None:
                    raise MaterialesNoDisponibles("Sin catálogo de Materiales.")
                catalogo = await self._materiales.materiales(token)
            except MaterialesNoDisponibles as error:
                if filtro_material:
                    raise ServicioNoDisponible("Materiales no responde y el pedido filtra por material.") from error
                avisos.append(AVISO_MATERIALES)

        filtro_cliente = [f for f in receta.filtros if f.campo == "cliente" and f.operador == "CONTIENE"]
        if rest and filtro_cliente and directorio is None:
            raise ServicioNoDisponible("Falta el directorio de clientes y el pedido filtra por cliente.")
        if rest and "cliente" in claves and directorio is None:
            avisos.append(AVISO_CLIENTES)

        if receta.conjunto == "PLANILLAS" and receta.filtros_de("periodo"):
            estados = [f.valor for f in receta.filtros_de("estado")]
            if not estados or any(v == "OBSERVADA" or (isinstance(v, tuple) and "OBSERVADA" in v) for v in estados):
                avisos.append(AVISO_PLANILLAS_SIN_PERIODO)

        ids_material = None
        if filtro_material and catalogo is not None:
            ids_material = _coincidencias(
                {m.id: (m.nombre, m.codigo) for m in catalogo.values()}, [str(f.valor) for f in filtro_material]
            )
        ids_cliente = None
        if filtro_cliente and directorio is not None:
            ids_cliente = _coincidencias(
                {i: (n,) for i, n in directorio.clientes.items()}, [str(f.valor) for f in filtro_cliente]
            )

        columnas = receta.columnas_salida()
        if ids_material == () or ids_cliente == ():
            return Resultado(columnas, [], False, avisos)  # sin coincidencias: vacío sin consultar
        filas = await self._ejecutor.ejecutar(ConsultaResuelta(receta, hoy, ids_material, ids_cliente))
        convertidas = [self._fila(fila, claves, rest, catalogo, directorio) for fila in filas.filas]
        return Resultado(columnas, convertidas, filas.truncado, avisos)

    @staticmethod
    def _fila(
        fila: tuple[Any, ...],
        claves: tuple[str, ...],
        rest: bool,
        catalogo: Mapping[str, MaterialCatalogo] | None,
        directorio: Directorio | None,
    ) -> Fila:
        salida: Fila = []
        for clave, valor in zip(claves, fila, strict=True):
            texto = valor_texto(valor)
            if rest and clave == "material":
                material = catalogo.get(str(valor)) if catalogo is not None and valor is not None else None
                salida.append(material.nombre if material else texto)
                salida.append(material.unidad if material else None)
                continue
            if rest and clave == "cliente" and directorio is not None and valor is not None:
                texto = directorio.clientes.get(str(valor), texto)
            salida.append(texto)
        return salida


def _coincidencias(nombres: Mapping[str, tuple[str, ...]], buscados: list[str]) -> tuple[str, ...]:
    """Ids cuyo nombre (o código) contiene cada texto buscado, sin distinguir mayúsculas ni tildes."""
    textos = [normalizar(b) for b in buscados]
    return tuple(
        sorted(i for i, valores in nombres.items() if all(any(t in normalizar(v) for v in valores) for t in textos))
    )
