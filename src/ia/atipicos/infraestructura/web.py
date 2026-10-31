"""Rutas REST de los consumos atípicos (403), las que consume el gateway.

- `GET /consumos-atipicos?obraId=&estado=&desde=&hasta=` · `admin`, `gerente`, `residente` (obras visibles)
- `GET /consumos-atipicos/resumen?obraIds=a,b,c` · `admin`, `gerente`, `residente` (hasta 100 obras)
- `PUT /consumos-atipicos/{id}/revision` · `admin`, `residente` asignado a la obra
"""

from collections.abc import Awaitable, Callable
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from ia.atipicos.aplicacion import errores
from ia.atipicos.aplicacion.consultas import ConsultarConsumosAtipicos
from ia.atipicos.aplicacion.revisar import RevisarConsumoAtipico
from ia.atipicos.dominio.consumo import ConsumoAtipico
from ia.atipicos.infraestructura.obras_sql import ObrasReportesSql
from ia.atipicos.infraestructura.repositorio_mongo import RepositorioAtipicosMongo
from ia.auth.dependencias import exigir_roles
from ia.auth.usuario import Usuario
from ia.comun.errores import ErrorApi, ErrorCampo
from ia.comun.tiempo import Reloj, RelojSistema

router = APIRouter(tags=["consumos-atipicos"])


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ConsumoAtipicoRespuesta(_Camel):
    id: str
    movimiento_id: str
    obra_id: str
    material_id: str
    partida_id: str
    cantidad: float
    periodo: str
    registrado_en: datetime
    puntaje: float
    motivos: list[str]
    estado_revision: str
    revisado_por: str | None
    revisado_en: datetime | None
    comentario: str | None
    version_modelo: str
    detectado_en: datetime

    @classmethod
    def desde(cls, consumo: ConsumoAtipico) -> "ConsumoAtipicoRespuesta":
        return cls(
            id=consumo.id,
            movimiento_id=consumo.id,
            obra_id=consumo.obra_id,
            material_id=consumo.material_id,
            partida_id=consumo.partida_id,
            cantidad=float(consumo.cantidad),
            periodo=consumo.periodo,
            registrado_en=consumo.registrado_en,
            puntaje=consumo.puntaje,
            motivos=list(consumo.motivos),
            estado_revision=consumo.estado_revision,
            revisado_por=consumo.revisado_por,
            revisado_en=consumo.revisado_en,
            comentario=consumo.comentario,
            version_modelo=consumo.version_modelo,
            detectado_en=consumo.detectado_en,
        )


class PendientesObra(_Camel):
    obra_id: str
    pendientes: int


class RevisionPedida(BaseModel):
    estado: str
    comentario: str | None = None


def _repositorio(request: Request) -> RepositorioAtipicosMongo:
    return RepositorioAtipicosMongo(request.app.state.ia_db)


def consultas(request: Request) -> ConsultarConsumosAtipicos:
    return ConsultarConsumosAtipicos(
        _repositorio(request), ObrasReportesSql(request.app.state.motor), request.app.state.config.zona_horaria
    )


def revision(request: Request) -> RevisarConsumoAtipico:
    reloj: Reloj = getattr(request.app.state, "reloj", None) or RelojSistema(request.app.state.config.zona_horaria)
    return RevisarConsumoAtipico(_repositorio(request), ObrasReportesSql(request.app.state.motor), reloj)


@router.get("/consumos-atipicos", response_model=list[ConsumoAtipicoRespuesta])
async def listar_consumos_atipicos(
    usuario: Usuario = Depends(exigir_roles("admin", "gerente", "residente")),  # noqa: B008
    caso: ConsultarConsumosAtipicos = Depends(consultas),  # noqa: B008
    obra_id: str | None = Query(None, alias="obraId"),
    estado: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
) -> list[ConsumoAtipicoRespuesta]:
    consumos = await _traducir(lambda: caso.listar(usuario, obra_id, estado, desde, hasta))
    return [ConsumoAtipicoRespuesta.desde(consumo) for consumo in consumos]


@router.get("/consumos-atipicos/resumen", response_model=list[PendientesObra])
async def resumen_consumos_atipicos(
    usuario: Usuario = Depends(exigir_roles("admin", "gerente", "residente")),  # noqa: B008
    caso: ConsultarConsumosAtipicos = Depends(consultas),  # noqa: B008
    obra_ids: str = Query(..., alias="obraIds"),
) -> list[PendientesObra]:
    pedidas = [obra_id.strip() for obra_id in obra_ids.split(",") if obra_id.strip()]
    filas = await _traducir(lambda: caso.resumen(usuario, pedidas))
    return [PendientesObra(obra_id=obra_id, pendientes=pendientes) for obra_id, pendientes in filas]


@router.put("/consumos-atipicos/{consumo_id}/revision", response_model=ConsumoAtipicoRespuesta)
async def revisar_consumo_atipico(
    consumo_id: str,
    pedido: RevisionPedida,
    usuario: Usuario = Depends(exigir_roles("admin", "residente")),  # noqa: B008
    caso: RevisarConsumoAtipico = Depends(revision),  # noqa: B008
) -> ConsumoAtipicoRespuesta:
    consumo = await _traducir(lambda: caso.revisar(usuario, consumo_id, pedido.estado, pedido.comentario))
    return ConsumoAtipicoRespuesta.desde(consumo)


# Errores de los casos de uso → Problem Details (códigos de docs/mensajes.md).
_ERRORES: dict[type[errores.ErrorAtipicos], tuple[int, str, str]] = {
    errores.DatosInvalidos: (400, "DATOS_INVALIDOS", "Datos inválidos"),
    errores.DemasiadasObras: (400, "DEMASIADAS_OBRAS", "Demasiadas obras"),
    errores.ObraNoEncontrada: (404, "OBRA_NO_ENCONTRADA", "Obra no encontrada"),
    errores.ConsumoNoEncontrado: (404, "CONSUMO_ATIPICO_NO_ENCONTRADO", "Consumo atípico no encontrado"),
    errores.RevisionYaRegistrada: (409, "REVISION_YA_REGISTRADA", "Revisión ya registrada"),
    errores.ComentarioInvalido: (422, "COMENTARIO_INVALIDO", "Revisa el comentario."),
}


async def _traducir[T](accion: Callable[[], Awaitable[T]]) -> T:
    try:
        return await accion()
    except errores.ErrorAtipicos as error:
        estado, codigo, titulo = _ERRORES[type(error)]
        campos = [ErrorCampo(campo="comentario", mensaje=error.mensaje)] if estado == 422 else []
        raise ErrorApi(estado=estado, codigo=codigo, titulo=titulo, detalle=error.mensaje, errores=campos) from error
