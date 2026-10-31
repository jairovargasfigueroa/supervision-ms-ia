"""Rutas REST del riesgo de retraso (402): las llama el gateway."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from ia.auth.dependencias import exigir_roles
from ia.auth.usuario import Usuario
from ia.comun.errores import ErrorApi, ErrorCampo, error_generico
from ia.riesgo.aplicacion.consultas import (
    MAXIMO_OBRAS,
    ConsultasRiesgo,
    DemasiadasObras,
    ModeloNoDisponible,
    ObraNoEncontrada,
)
from ia.riesgo.aplicacion.puertos import Prediccion
from ia.riesgo.dominio.variables import SIN_DATOS
from ia.riesgo.infraestructura.fuente_sql import VisibilidadObrasSql
from ia.riesgo.infraestructura.predicciones_mongo import AlmacenPrediccionesMongo

router = APIRouter(tags=["riesgo"])
LECTORES = exigir_roles("admin", "gerente", "residente")


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class FactorRespuesta(_Camel):
    variable: str
    descripcion: str
    peso: float


class PrediccionRespuesta(_Camel):
    obra_id: str
    probabilidad: float | None
    nivel: str
    factores: list[FactorRespuesta]
    version_modelo: str | None
    calculada_en: datetime | None


class PuntoHistorial(_Camel):
    probabilidad: float
    calculada_en: datetime


class PrediccionObraRespuesta(PrediccionRespuesta):
    historial: list[PuntoHistorial]


class MetricasRespuesta(_Camel):
    auc_roc: float
    auc_roc_regla: float
    recall_medio: float
    precision_alto: float
    brier: float


class UmbralesRespuesta(_Camel):
    medio: float
    alto: float


class VariableRespuesta(_Camel):
    nombre: str
    descripcion: str


class ModeloRespuesta(_Camel):
    version_modelo: str
    entrenado_en: datetime
    datos: str
    filas_entrenamiento: int
    metricas: MetricasRespuesta
    umbrales: UmbralesRespuesta
    variables: list[VariableRespuesta]


def consultas(request: Request) -> ConsultasRiesgo:
    estado = request.app.state
    return ConsultasRiesgo(
        VisibilidadObrasSql(estado.motor), AlmacenPrediccionesMongo(estado.ia_db), estado.modelo_riesgo
    )


def _respuesta(obra_id: str, p: Prediccion | None) -> PrediccionRespuesta:
    if p is None:
        return PrediccionRespuesta(
            obra_id=obra_id, probabilidad=None, nivel=SIN_DATOS, factores=[], version_modelo=None, calculada_en=None
        )
    return PrediccionRespuesta(
        obra_id=obra_id,
        probabilidad=p.riesgo_retraso,
        nivel=p.nivel,
        factores=[] if p.riesgo_retraso is None else [FactorRespuesta(**vars(f)) for f in p.factores],
        version_modelo=p.version_modelo,
        calculada_en=p.calculada_en,
    )


@router.get("/predicciones", response_model=list[PrediccionRespuesta])
async def listar_predicciones(
    obra_ids: str | None = Query(default=None, alias="obraIds"),
    usuario: Usuario = Depends(LECTORES),  # noqa: B008
    caso: ConsultasRiesgo = Depends(consultas),  # noqa: B008
) -> list[PrediccionRespuesta]:
    ids = [parte.strip() for parte in (obra_ids or "").split(",") if parte.strip()]
    if not ids:
        raise _datos_invalidos("obraIds", "Indica de 1 a 100 ids de obra separados por coma.")
    if len(dict.fromkeys(ids)) > MAXIMO_OBRAS:
        raise ErrorApi(400, "DEMASIADAS_OBRAS", "Demasiadas obras", f"Se pueden pedir hasta {MAXIMO_OBRAS} obras.")
    if not all(_es_uuid(i) for i in ids):
        raise _datos_invalidos("obraIds", "Cada id de obra debe ser un UUID.")
    try:
        predicciones = await caso.predicciones(usuario, [str(uuid.UUID(i)) for i in ids])
    except DemasiadasObras as error:
        raise ErrorApi(400, "DEMASIADAS_OBRAS", "Demasiadas obras") from error
    return [_respuesta(p.obra_id, p) for p in predicciones]


@router.get("/obras/{obra_id}/prediccion", response_model=PrediccionObraRespuesta)
async def prediccion_obra(
    obra_id: str,
    historial: int = Query(default=30, ge=1, le=90),
    usuario: Usuario = Depends(LECTORES),  # noqa: B008
    caso: ConsultasRiesgo = Depends(consultas),  # noqa: B008
) -> PrediccionObraRespuesta:
    no_encontrada = ErrorApi(404, "OBRA_NO_ENCONTRADA", "Obra no encontrada", "La obra no existe o no la puedes ver.")
    if not _es_uuid(obra_id):
        raise no_encontrada
    try:
        resultado = await caso.prediccion_obra(usuario, str(uuid.UUID(obra_id)), historial)
    except ObraNoEncontrada as error:
        raise no_encontrada from error
    base = _respuesta(str(uuid.UUID(obra_id)), resultado.prediccion)
    return PrediccionObraRespuesta(
        **base.model_dump(),
        historial=[
            PuntoHistorial(probabilidad=p.riesgo_retraso, calculada_en=p.calculada_en)
            for p in resultado.historial
            if p.riesgo_retraso is not None
        ],
    )


@router.get("/modelos/riesgo-retraso", response_model=ModeloRespuesta)
async def modelo_riesgo(
    _: Usuario = Depends(LECTORES),  # noqa: B008
    caso: ConsultasRiesgo = Depends(consultas),  # noqa: B008
) -> ModeloRespuesta:
    try:
        info = caso.modelo()
    except ModeloNoDisponible as error:
        raise ErrorApi(
            503, "MODELO_NO_DISPONIBLE", "Modelo no disponible", "No hay un modelo de riesgo de retraso cargado."
        ) from error
    m = info.metricas
    return ModeloRespuesta(
        version_modelo=info.version,
        entrenado_en=info.entrenado_en,
        datos=info.datos,
        filas_entrenamiento=info.filas_entrenamiento,
        metricas=MetricasRespuesta(
            auc_roc=m["aucRoc"],
            auc_roc_regla=m["aucRocRegla"],
            recall_medio=m["recallMedio"],
            precision_alto=m["precisionAlto"],
            brier=m["brier"],
        ),
        umbrales=UmbralesRespuesta(medio=info.umbrales["medio"], alto=info.umbrales["alto"]),
        variables=[VariableRespuesta(nombre=n, descripcion=d) for n, d in info.variables],
    )


def _es_uuid(texto: str) -> bool:
    try:
        uuid.UUID(texto)
    except ValueError:
        return False
    return True


def _datos_invalidos(campo: str, mensaje: str) -> ErrorApi:
    error = error_generico(400, mensaje)
    error.errores = [ErrorCampo(campo=campo, mensaje=mensaje)]
    return error
