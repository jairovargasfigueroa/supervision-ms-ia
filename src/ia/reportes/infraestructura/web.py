"""Rutas REST de `reportes`: `GET /reportes/estado` (rol `admin`)."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from ia.auth.dependencias import exigir_roles
from ia.auth.usuario import Usuario
from ia.reportes.aplicacion.estado import EstadoReportes
from ia.reportes.infraestructura.repositorio_sql import BaseReportesSql

router = APIRouter(tags=["reportes"])


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class UltimoEvento(_Camel):
    tipo: str
    aplicado_en: datetime


class CargaInicialRespuesta(_Camel):
    carga_id: str
    estado: str
    iniciada_en: datetime
    terminada_en: datetime | None
    obras: int
    filas: dict[str, int]
    error: str | None


class EstadoRespuesta(_Camel):
    tablas: dict[str, int]
    ultimos_eventos: list[UltimoEvento]
    carga_inicial: CargaInicialRespuesta | None


def estado_reportes(request: Request) -> EstadoReportes:
    return EstadoReportes(BaseReportesSql(request.app.state.motor))


@router.get("/reportes/estado", response_model=EstadoRespuesta)
async def consultar_estado(
    _: Usuario = Depends(exigir_roles("admin")),  # noqa: B008
    caso: EstadoReportes = Depends(estado_reportes),  # noqa: B008
) -> EstadoRespuesta:
    estado = await caso.consultar()
    carga = estado.carga_inicial
    return EstadoRespuesta(
        tablas=estado.tablas,
        ultimos_eventos=[UltimoEvento(tipo=tipo, aplicado_en=momento) for tipo, momento in estado.ultimos_eventos],
        carga_inicial=None
        if carga is None
        else CargaInicialRespuesta(
            carga_id=carga.carga_id,
            estado=carga.estado,
            iniciada_en=carga.iniciada_en,
            terminada_en=carga.terminada_en,
            obras=carga.obras,
            filas=carga.filas,
            error=carga.error,
        ),
    )
