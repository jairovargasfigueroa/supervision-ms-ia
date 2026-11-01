"""Herramientas del servidor MCP de IA (501): solo lectura, con lo que solo el servicio de IA sabe.

- `riesgo_retraso`: última predicción de la 402 de las obras `EN_EJECUCION`.
- `consumos_atipicos`: consumos atípicos de la 403 (el agente no los revisa: no hay herramienta que escriba).
- `consultar_reporte`: la receta de la 503 en modo `MCP`, con el mismo `validar_receta` y `MotorReportes`.

Cada una llama a un caso de uso de consulta de su módulo. Las dependencias (motor, `ia_db`, reloj) se leen del
estado de la app en cada llamada (las crea el *lifespan*).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from pydantic.alias_generators import to_camel
from starlette.datastructures import State

from ia.atipicos.aplicacion.consultas import ConsultarConsumosAtipicos, ConsumosFiltrados
from ia.atipicos.aplicacion.errores import ObraNoEncontrada as AtipicoObraNoEncontrada
from ia.atipicos.infraestructura.obras_sql import ObrasReportesSql
from ia.atipicos.infraestructura.repositorio_mongo import RepositorioAtipicosMongo
from ia.comun.tiempo import Reloj, RelojSistema
from ia.constructor_reportes.aplicacion.motor import MotorReportes
from ia.constructor_reportes.aplicacion.puertos import ReportesNoDisponibles
from ia.constructor_reportes.dominio.catalogo import CATALOGO, MCP
from ia.constructor_reportes.dominio.receta import RecetaReporte
from ia.constructor_reportes.dominio.resultado import describir_aplicado
from ia.constructor_reportes.dominio.validacion import (
    LIMITE_MCP,
    IntencionNoReconocida,
    RecetaInvalida,
    validar_receta,
)
from ia.constructor_reportes.infraestructura.atipicos_mongo import ConsultaAtipicosMongo
from ia.constructor_reportes.infraestructura.ejecutor_sql import EjecutorReportes, EjecutorSql
from ia.mcp.servidor.servidor import (
    ARGUMENTO_INVALIDO,
    ERROR_INTERNO,
    OBRA_NO_ENCONTRADA,
    PROBLEMAS_MAXIMOS,
    ErrorHerramienta,
    Herramienta,
)
from ia.riesgo.aplicacion.consultas import ObraNoEncontrada as RiesgoObraNoEncontrada
from ia.riesgo.aplicacion.consultas import PrediccionesEnEjecucion, PrediccionesFiltradas
from ia.riesgo.aplicacion.puertos import Prediccion
from ia.riesgo.infraestructura.fuente_sql import ObrasResumenSql
from ia.riesgo.infraestructura.predicciones_mongo import AlmacenPrediccionesMongo

MOTIVO_MAXIMO = 200


# Argumentos en camelCase y sin campos de más (`additionalProperties: false`). Los modelos de entrada y salida no
# llevan docstring: pasaría a la descripción del esquema.
class Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, frozen=True)


class Salida(BaseModel):
    model_config = ConfigDict(extra="forbid", alias_generator=to_camel, populate_by_name=True, serialize_by_alias=True)


def reloj_de(estado: State) -> Reloj:
    reloj: Reloj | None = getattr(estado, "reloj", None)
    return reloj or RelojSistema(estado.config.zona_horaria)


# --- riesgo_retraso (402) -------------------------------------------------------------------------------------------

Nivel = Literal["BAJO", "MEDIO", "ALTO", "SIN_DATOS"]
NIVELES: tuple[Nivel, ...] = ("BAJO", "MEDIO", "ALTO", "SIN_DATOS")


class EntradaRiesgo(Entrada):
    obra_ids: Annotated[list[UUID], Field(min_length=1, max_length=50)] | None = None
    niveles: Annotated[list[Nivel], Field(min_length=1, max_length=4)] | None = None
    limite: StrictInt = Field(default=50, ge=1, le=50)


class FactorSalida(Salida):
    variable: str
    descripcion: str
    peso: float


class PrediccionSalida(Salida):
    obra_id: str
    obra_nombre: str
    probabilidad: float | None
    nivel: Nivel
    factores: list[FactorSalida]
    version_modelo: str | None
    calculada_en: datetime


class SalidaRiesgo(Salida):
    generado_en: datetime
    predicciones: list[PrediccionSalida]
    total: int
    truncado: bool


def _factores(prediccion: Prediccion) -> list[FactorSalida]:
    """Como `GET /predicciones`: sin factores si es `SIN_DATOS`."""
    if prediccion.riesgo_retraso is None:
        return []
    return [FactorSalida(variable=f.variable, descripcion=f.descripcion, peso=f.peso) for f in prediccion.factores]


def herramienta_riesgo(estado: State) -> Herramienta:
    async def ejecutar(entrada: EntradaRiesgo) -> SalidaRiesgo:
        caso = PrediccionesEnEjecucion(ObrasResumenSql(estado.motor), AlmacenPrediccionesMongo(estado.ia_db))
        try:
            resultado: PrediccionesFiltradas = await caso.consultar(
                None if entrada.obra_ids is None else [str(i) for i in entrada.obra_ids],
                frozenset(entrada.niveles or NIVELES),
                entrada.limite,
            )
        except RiesgoObraNoEncontrada as error:
            raise ErrorHerramienta(OBRA_NO_ENCONTRADA, f"La obra {error} no existe.") from error
        predicciones = [
            PrediccionSalida(
                obra_id=p.obra.obra_id,
                obra_nombre=p.obra.nombre,
                probabilidad=p.prediccion.riesgo_retraso,
                nivel=p.prediccion.nivel,
                factores=_factores(p.prediccion),
                version_modelo=p.prediccion.version_modelo,
                calculada_en=p.prediccion.calculada_en,
            )
            for p in resultado.predicciones
        ]
        return SalidaRiesgo(
            generado_en=reloj_de(estado).ahora(),
            predicciones=predicciones,
            total=resultado.total,
            truncado=resultado.total > len(predicciones),
        )

    return Herramienta(
        nombre="riesgo_retraso",
        descripcion=(
            "Devuelve la última predicción de riesgo de retraso (modelo de la 402) de las obras en ejecución: "
            "probabilidad, nivel y hasta 3 factores. Sirve para encontrar obras que pueden atrasarse aunque hoy no "
            "lo estén."
        ),
        entrada=EntradaRiesgo,
        salida=SalidaRiesgo,
        ejecutar=ejecutar,
        filas=lambda s: len(s.predicciones),
    )


# --- consumos_atipicos (403) ----------------------------------------------------------------------------------------

EstadoRevision = Literal["PENDIENTE", "NORMAL", "CONFIRMADO"]


class EntradaAtipicos(Entrada):
    obra_id: UUID | None = None
    estados: Annotated[list[EstadoRevision], Field(min_length=1, max_length=3)] | None = None
    dias: StrictInt = Field(default=30, ge=1, le=90)
    limite: StrictInt = Field(default=50, ge=1, le=50)


class ConsumoSalida(Salida):
    movimiento_id: str
    obra_id: str
    obra_nombre: str | None
    material_id: str
    partida_id: str
    cantidad: float
    periodo: str
    registrado_en: datetime
    puntaje: float
    motivos: list[str]
    estado_revision: EstadoRevision


class SalidaAtipicos(Salida):
    consumos: list[ConsumoSalida]
    total: int
    truncado: bool


def herramienta_atipicos(estado: State) -> Herramienta:
    async def ejecutar(entrada: EntradaAtipicos) -> SalidaAtipicos:
        caso = ConsultarConsumosAtipicos(
            RepositorioAtipicosMongo(estado.ia_db), ObrasReportesSql(estado.motor), estado.config.zona_horaria
        )
        try:
            resultado: ConsumosFiltrados = await caso.para_agentes(
                None if entrada.obra_id is None else str(entrada.obra_id),
                list(entrada.estados or ["PENDIENTE"]),
                entrada.dias,
                reloj_de(estado).hoy(),
                entrada.limite,
            )
        except AtipicoObraNoEncontrada as error:
            raise ErrorHerramienta(OBRA_NO_ENCONTRADA, "La obra no existe.") from error
        consumos = [
            ConsumoSalida(
                movimiento_id=c.consumo.id,
                obra_id=c.consumo.obra_id,
                obra_nombre=c.obra_nombre,
                material_id=c.consumo.material_id,
                partida_id=c.consumo.partida_id,
                cantidad=float(c.consumo.cantidad),
                periodo=c.consumo.periodo,
                registrado_en=c.consumo.registrado_en,
                puntaje=c.consumo.puntaje,
                motivos=[m[:MOTIVO_MAXIMO] for m in c.consumo.motivos],
                estado_revision=c.consumo.estado_revision,
            )
            for c in resultado.consumos
        ]
        return SalidaAtipicos(consumos=consumos, total=resultado.total, truncado=resultado.total > len(consumos))

    return Herramienta(
        nombre="consumos_atipicos",
        descripcion=(
            "Devuelve las salidas de material que el modelo de la 403 marcó como atípicas, con su puntaje y motivos. "
            "Por defecto, las pendientes de revisión de los últimos 30 días. Solo lectura: la revisión la hace una "
            "persona."
        ),
        entrada=EntradaAtipicos,
        salida=SalidaAtipicos,
        ejecutar=ejecutar,
        filas=lambda s: len(s.consumos),
    )


# --- consultar_reporte (motor de la 503) ----------------------------------------------------------------------------

ConjuntoMcp = Literal["OBRAS", "CONSUMO_MATERIAL", "MOVIMIENTOS", "COBROS", "PLANILLAS", "CONSUMOS_ATIPICOS"]


# La receta de la 503 en modo `MCP`, en el primer nivel: sin `NINGUNO`, límite de 1 a 50 y solo `agruparPor` (sin el
# nombre de Python `agrupar_por`). Que cada clave exista en el conjunto lo revisa `validar_receta`. Sin docstring: el
# docstring de un modelo pasa a la descripción del esquema que lee el modelo de lenguaje.
class EntradaReporte(RecetaReporte):
    model_config = ConfigDict(extra="forbid", populate_by_name=False, frozen=True)

    conjunto: ConjuntoMcp
    limite: StrictInt | None = Field(default=None, ge=1, le=LIMITE_MCP)


class AplicadoSalida(Salida):
    nombre: str
    valor: str


class ColumnaSalida(Salida):
    clave: str
    titulo: str
    tipo: str


class SalidaReporte(Salida):
    conjunto: str
    titulo: str
    aplicado: list[AplicadoSalida]
    columnas: list[ColumnaSalida]
    filas: list[list[str | None]]
    total_filas: int
    truncado: bool
    avisos: list[str]


def crear_motor_mcp(estado: State) -> MotorReportes:
    """El mismo `MotorReportes` de `POST /consultas-reporte` (usuario `ia_reportes_lector`), sin catálogo de
    Materiales: en el modo `MCP` los materiales y clientes van por id."""
    sql = EjecutorSql(estado.motor_lector)
    return MotorReportes(EjecutorReportes(sql, ConsultaAtipicosMongo(estado.ia_db, sql, estado.config.zona_horaria)))


def herramienta_reporte(estado: State) -> Herramienta:
    async def ejecutar(entrada: EntradaReporte) -> SalidaReporte:
        hoy = reloj_de(estado).hoy()
        try:
            receta = validar_receta(entrada.model_dump(by_alias=True, exclude_none=True), MCP, hoy)
        except IntencionNoReconocida as error:
            raise ErrorHerramienta(ARGUMENTO_INVALIDO, f"Receta inválida: {error}") from error
        except RecetaInvalida as error:
            problemas = [f"{e.campo}: {e.mensaje}" for e in error.errores[:PROBLEMAS_MAXIMOS]]
            raise ErrorHerramienta(ARGUMENTO_INVALIDO, "Receta inválida: " + "; ".join(problemas)) from error
        try:
            resultado = await crear_motor_mcp(estado).ejecutar(receta, hoy)
        except ReportesNoDisponibles as error:
            raise ErrorHerramienta(
                ERROR_INTERNO, "La base de reportes no respondió o la consulta pasó de 5 s."
            ) from error
        return SalidaReporte(
            conjunto=receta.conjunto,
            titulo=CATALOGO[receta.conjunto].titulo,
            aplicado=[AplicadoSalida(nombre=e.nombre, valor=e.valor) for e in describir_aplicado(receta)],
            columnas=[ColumnaSalida(clave=c.clave, titulo=c.titulo, tipo=c.tipo) for c in resultado.columnas],
            filas=resultado.filas,
            total_filas=resultado.total_filas,
            truncado=resultado.truncado,
            avisos=resultado.avisos,
        )

    return Herramienta(
        nombre="consultar_reporte",
        descripcion=(
            "Devuelve un reporte de datos históricos que cruzan servicios (obras, material usado, movimientos, "
            "cobros, planillas aprobadas u observadas, consumos atípicos), armado con una receta del catálogo. Los "
            "datos pueden ir hasta un día atrasados; para el dato de hoy usa las otras herramientas."
        ),
        entrada=EntradaReporte,
        salida=SalidaReporte,
        ejecutar=ejecutar,
        filas=lambda s: len(s.filas),
    )


def herramientas_ia(estado: State) -> list[Herramienta]:
    """Las herramientas del contrato (501), en orden."""
    return [herramienta_reporte(estado), herramienta_riesgo(estado), herramienta_atipicos(estado)]
