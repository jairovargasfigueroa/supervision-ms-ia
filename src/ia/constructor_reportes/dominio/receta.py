"""Receta de un reporte: lo único que entrega el modelo de lenguaje (nunca SQL).

`RecetaReporte` valida la **forma** (tipos, enums, largos de listas, rango del límite y sin campos de más, en todos
los niveles). Que cada clave exista en el conjunto y en el modo, y los valores de cada filtro, lo revisa
`validar_receta` contra el catálogo.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

CONJUNTOS = ("OBRAS", "CONSUMO_MATERIAL", "MOVIMIENTOS", "COBROS", "PLANILLAS", "CONSUMOS_ATIPICOS")
NINGUNO = "NINGUNO"

Conjunto = Literal["OBRAS", "CONSUMO_MATERIAL", "MOVIMIENTOS", "COBROS", "PLANILLAS", "CONSUMOS_ATIPICOS", "NINGUNO"]
Operador = Literal["IGUAL", "EN", "CONTIENE", "DESDE", "HASTA"]
Funcion = Literal["CONTEO", "SUMA", "PROMEDIO", "MAXIMO", "MINIMO"]
Direccion = Literal["ASC", "DESC"]

LIMITE_REST = 100
CLAVE = Field(min_length=1, max_length=40)


class _Estricto(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class FiltroReceta(_Estricto):
    campo: StrictStr = CLAVE
    operador: Operador
    valor: StrictBool | StrictInt | StrictStr | Annotated[list[StrictStr], Field(min_length=1, max_length=20)] = Field(
        description="texto, número, booleano o lista de 1 a 20 textos"
    )


class MetricaReceta(_Estricto):
    funcion: Funcion
    campo: StrictStr | None = Field(default=None, max_length=40, description="clave de medida (no va con CONTEO)")


class OrdenReceta(_Estricto):
    por: StrictStr = CLAVE
    direccion: Direccion = "ASC"


class RecetaReporte(_Estricto):
    conjunto: Conjunto
    columnas: list[StrictStr] | None = Field(default=None, min_length=1, max_length=10)
    filtros: list[FiltroReceta] | None = Field(default=None, max_length=10)
    agrupar_por: list[StrictStr] | None = Field(default=None, alias="agruparPor", min_length=1, max_length=3)
    metricas: list[MetricaReceta] | None = Field(default=None, min_length=1, max_length=4)
    orden: list[OrdenReceta] | None = Field(default=None, min_length=1, max_length=2)
    limite: StrictInt | None = Field(default=None, ge=1, le=LIMITE_REST)


def esquema_receta() -> dict[str, Any]:
    """JSON Schema de la receta (pasa luego por `adaptar_esquema` del proveedor)."""
    return RecetaReporte.model_json_schema(by_alias=True)
