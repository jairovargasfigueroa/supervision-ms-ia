"""Catálogo del host (501): qué herramienta vive en qué servidor y qué entrada espera el contrato.

- `CONTRATO`: las 9 herramientas de los cuatro servidores (Obras, Materiales, Finanzas e IA) con su modelo de entrada
  (Pydantic, `extra="forbid"`), sacado del diseño de la 501. El host valida con él los argumentos que pide el modelo
  de lenguaje antes de llamar (502).
- `aceptadas`: compara lo que publicó un servidor (`tools/list`) con el contrato. Una herramienta que no está en el
  contrato, que llega desde otro servidor o cuyo esquema de entrada no coincide (propiedades y obligatorias) se
  ignora con una advertencia.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from pydantic.alias_generators import to_camel

from ia.config import ConfiguracionWorker
from ia.mcp.cliente.cliente import HerramientaPublicada
from ia.mcp.servidor.herramientas import EntradaAtipicos, EntradaReporte, EntradaRiesgo

registro = logging.getLogger("ia.mcp.cliente")

OBRAS = "obras"
MATERIALES = "materiales"
FINANZAS = "finanzas"
IA = "ia"
SERVIDORES = (OBRAS, MATERIALES, FINANZAS, IA)

Periodo = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
IdTexto = Annotated[str, Field(min_length=1, max_length=64)]


class _Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, frozen=True)


class EntradaAvanceObra(_Entrada):
    obra_id: UUID | None = None
    periodo: Periodo | None = None


class EntradaBuscarDiario(_Entrada):
    obra_id: UUID
    desde: date | None = None
    hasta: date | None = None
    texto: Annotated[str, Field(min_length=2, max_length=50)] | None = None
    limite: StrictInt | None = Field(default=None, ge=1, le=30)


EstadoPlanilla = Literal["BORRADOR", "PRESENTADA", "APROBANDO", "APROBADA", "OBSERVADA"]


class EntradaPlanillasEstado(_Entrada):
    obra_id: UUID | None = None
    estados: Annotated[list[EstadoPlanilla], Field(min_length=1, max_length=5)] | None = None
    desde_periodo: Periodo | None = None
    limite: StrictInt | None = Field(default=None, ge=1, le=50)


class EntradaStockObra(_Entrada):
    obra_id: IdTexto | None = None
    estados: Annotated[list[Literal["BAJO", "JUSTO", "OK"]], Field(min_length=1, max_length=3)] | None = None
    limite: StrictInt | None = Field(default=None, ge=1, le=100)


class EntradaConsumosPartida(_Entrada):
    obra_id: IdTexto
    periodo: Periodo
    partida_id: IdTexto | None = None


class EntradaCobrosPorVencer(_Entrada):
    dias: StrictInt | None = Field(default=None, ge=1, le=90)
    incluir_vencidos: StrictBool | None = None
    obra_id: UUID | None = None


@dataclass(frozen=True)
class HerramientaContrato:
    nombre: str
    servidor: str
    entrada: type[BaseModel]

    @property
    def propiedades(self) -> frozenset[str]:
        return frozenset(self.entrada.model_json_schema(by_alias=True).get("properties", {}))

    @property
    def obligatorias(self) -> frozenset[str]:
        return frozenset(self.entrada.model_json_schema(by_alias=True).get("required", []))


CONTRATO: Mapping[str, HerramientaContrato] = {
    h.nombre: h
    for h in (
        HerramientaContrato("avance_obra", OBRAS, EntradaAvanceObra),
        HerramientaContrato("buscar_diario", OBRAS, EntradaBuscarDiario),
        HerramientaContrato("planillas_estado", OBRAS, EntradaPlanillasEstado),
        HerramientaContrato("stock_obra", MATERIALES, EntradaStockObra),
        HerramientaContrato("consumos_partida", MATERIALES, EntradaConsumosPartida),
        HerramientaContrato("cobros_por_vencer", FINANZAS, EntradaCobrosPorVencer),
        HerramientaContrato("consultar_reporte", IA, EntradaReporte),
        HerramientaContrato("riesgo_retraso", IA, EntradaRiesgo),
        HerramientaContrato("consumos_atipicos", IA, EntradaAtipicos),
    )
}


def urls_servidores(config: ConfiguracionWorker) -> dict[str, str]:
    return {
        OBRAS: config.obras_mcp_url,
        MATERIALES: config.materiales_mcp_url,
        FINANZAS: config.finanzas_mcp_url,
        IA: config.propio_mcp_url,
    }


def servidor_de(herramienta: str) -> str | None:
    contrato = CONTRATO.get(herramienta)
    return None if contrato is None else contrato.servidor


def coincide(contrato: HerramientaContrato, esquema: Mapping[str, Any]) -> bool:
    """Mismo esquema de entrada: objeto con las mismas propiedades y las mismas obligatorias."""
    propiedades = esquema.get("properties")
    if esquema.get("type") != "object" or not isinstance(propiedades, Mapping):
        return False
    obligatorias = esquema.get("required") or []
    return frozenset(propiedades) == contrato.propiedades and frozenset(obligatorias) == contrato.obligatorias


def aceptadas(servidor: str, publicadas: Sequence[HerramientaPublicada]) -> list[HerramientaPublicada]:
    """Las publicadas que están en el contrato para ese servidor y con el mismo esquema; las demás, con advertencia."""
    validas: list[HerramientaPublicada] = []
    for herramienta in publicadas:
        contrato = CONTRATO.get(herramienta.nombre)
        if contrato is None or contrato.servidor != servidor:
            registro.warning(
                "El servidor MCP %s publica la herramienta %r, que no está en el contrato: se ignora.",
                servidor,
                herramienta.nombre[:80],
            )
            continue
        if not coincide(contrato, herramienta.esquema_entrada):
            registro.warning(
                "El servidor MCP %s publica %s con otro esquema de entrada: se ignora.", servidor, herramienta.nombre
            )
            continue
        validas.append(herramienta)
    return validas
