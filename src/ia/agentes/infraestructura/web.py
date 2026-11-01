"""Rutas REST de los agentes (502): las llama el gateway con el token del usuario. JSON en camelCase.

- `GET /agentes` · `POST /agentes/{agente}/ejecuciones` · `GET /ejecuciones` · `GET /ejecuciones/{id}`
- `GET /hallazgos` · `GET /sugerencias` · `POST /sugerencias/{id}/aprobar` · `POST /sugerencias/{id}/rechazar`
"""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.alias_generators import to_camel

from ia.agentes.aplicacion.casos import (
    AgenteInvalido,
    CasosAgentes,
    EjecucionNoEncontrada,
    HallazgoVigente,
    SugerenciaNoEncontrada,
    SugerenciaYaDecidida,
    TextoInvalido,
)
from ia.agentes.aplicacion.puertos import EjecucionEnCurso
from ia.agentes.dominio.definiciones import AGENTES, texto_accion
from ia.agentes.dominio.ejecucion import ESTADOS_EJECUCION, ESTADOS_SUGERENCIA, Ejecucion, HallazgoGuardado, Sugerencia
from ia.agentes.infraestructura.repositorio_mongo import RepositorioEjecucionesMongo, RepositorioSugerenciasMongo
from ia.atipicos.infraestructura.obras_sql import ObrasReportesSql
from ia.auth.dependencias import exigir_roles
from ia.auth.usuario import Usuario
from ia.comun.errores import ErrorApi, ErrorCampo, error_generico
from ia.comun.tiempo import Reloj, RelojSistema

router = APIRouter(tags=["agentes"])
LECTORES = exigir_roles("admin", "gerente", "residente")
DIRECCION = exigir_roles("admin", "gerente")


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class EjecucionCortaRespuesta(_Camel):
    id: str
    agente: str
    origen: str
    estado: str
    iniciada_en: datetime
    terminada_en: datetime | None
    cantidad_hallazgos: int

    @classmethod
    def desde(cls, e: Ejecucion) -> "EjecucionCortaRespuesta":
        return cls(
            id=e.id,
            agente=e.agente,
            origen=e.origen,
            estado=e.estado,
            iniciada_en=e.iniciada_en,
            terminada_en=e.terminada_en,
            cantidad_hallazgos=len(e.hallazgos),
        )


class AgenteRespuesta(_Camel):
    agente: str
    nombre: str
    mision: str
    herramientas: list[str]
    ultima_ejecucion: EjecucionCortaRespuesta | None
    sugerencias_pendientes: int


class ErrorRespuesta(_Camel):
    codigo: str
    mensaje: str


class PasoRespuesta(_Camel):
    numero: int
    servidor: str
    herramienta: str
    argumentos: str
    resultado_resumen: str
    en: datetime
    duracion_ms: int
    error: str | None


class EvidenciaRespuesta(_Camel):
    herramienta: str
    dato: str


class HallazgoRespuesta(_Camel):
    obra_id: str
    obra_nombre: str
    tipo: str
    severidad: str
    situacion: str
    causa_probable: str
    evidencia: list[EvidenciaRespuesta]

    @classmethod
    def campos(cls, h: HallazgoGuardado) -> dict[str, Any]:
        return {
            "obra_id": h.obra_id,
            "obra_nombre": h.obra_nombre,
            "tipo": h.tipo,
            "severidad": h.severidad,
            "situacion": h.situacion,
            "causa_probable": h.causa_probable,
            "evidencia": [EvidenciaRespuesta(herramienta=e.herramienta, dato=e.dato) for e in h.evidencia],
        }


class HallazgoVigenteRespuesta(HallazgoRespuesta):
    ejecucion_id: str
    agente: str
    detectado_en: datetime

    @classmethod
    def desde(cls, v: HallazgoVigente) -> "HallazgoVigenteRespuesta":
        assert v.ejecucion.terminada_en is not None
        return cls(
            **cls.campos(v.hallazgo),
            ejecucion_id=v.ejecucion.id,
            agente=v.ejecucion.agente,
            detectado_en=v.ejecucion.terminada_en,
        )


class EjecucionRespuesta(_Camel):
    id: str
    agente: str
    origen: str
    estado: str
    solicitada_por: str | None
    iniciada_en: datetime
    terminada_en: datetime | None
    proveedor_llm: str
    modelo: str
    resumen: str | None
    error: ErrorRespuesta | None
    pasos: list[PasoRespuesta]
    hallazgos: list[HallazgoRespuesta]

    @classmethod
    def desde(cls, e: Ejecucion) -> "EjecucionRespuesta":
        return cls(
            id=e.id,
            agente=e.agente,
            origen=e.origen,
            estado=e.estado,
            solicitada_por=e.solicitada_por,
            iniciada_en=e.iniciada_en,
            terminada_en=e.terminada_en,
            proveedor_llm=e.proveedor_llm,
            modelo=e.modelo,
            resumen=e.resumen,
            error=None if e.error is None else ErrorRespuesta(codigo=e.error.codigo, mensaje=e.error.mensaje),
            pasos=[PasoRespuesta(**vars(p)) for p in e.pasos],
            hallazgos=[HallazgoRespuesta(**HallazgoRespuesta.campos(h)) for h in e.hallazgos],
        )


class SugerenciaRespuesta(_Camel):
    id: str
    ejecucion_id: str
    agente: str
    obra_id: str
    obra_nombre: str
    accion: str
    accion_texto: str
    descripcion: str
    estado: str
    creada_en: datetime
    decidida_por: str | None
    decidida_en: datetime | None
    comentario: str | None

    @classmethod
    def desde(cls, s: Sugerencia) -> "SugerenciaRespuesta":
        return cls(
            id=s.id,
            ejecucion_id=s.ejecucion_id,
            agente=s.agente,
            obra_id=s.obra_id,
            obra_nombre=s.obra_nombre,
            accion=s.accion,
            accion_texto=texto_accion(s.agente, s.accion),
            descripcion=s.descripcion,
            estado=s.estado,
            creada_en=s.creada_en,
            decidida_por=s.decidida_por,
            decidida_en=s.decidida_en,
            comentario=s.comentario,
        )


class CuerpoAprobar(BaseModel):
    model_config = ConfigDict(extra="ignore")
    comentario: str | None = None


class CuerpoRechazar(BaseModel):
    model_config = ConfigDict(extra="ignore")
    motivo: str | None = None


def _reloj(request: Request) -> Reloj:
    reloj: Reloj | None = getattr(request.app.state, "reloj", None)
    return reloj or RelojSistema(request.app.state.config.zona_horaria)


def casos(request: Request) -> CasosAgentes:
    estado = request.app.state
    modelo = estado.modelo_lenguaje
    return CasosAgentes(
        RepositorioEjecucionesMongo(estado.ia_db),
        RepositorioSugerenciasMongo(estado.ia_db),
        ObrasReportesSql(estado.motor),
        _reloj(request),
        modelo.proveedor,
        modelo.modelo,
    )


@router.get("/agentes", response_model=list[AgenteRespuesta], response_model_by_alias=True)
async def get_agentes(
    usuario: Usuario = Depends(LECTORES),  # noqa: B008
    caso: CasosAgentes = Depends(casos),  # noqa: B008
) -> list[AgenteRespuesta]:
    return [
        AgenteRespuesta(
            agente=i.definicion.agente,
            nombre=i.definicion.nombre,
            mision=i.definicion.mision,
            herramientas=list(i.definicion.herramientas),
            ultima_ejecucion=None if i.ultima is None else EjecucionCortaRespuesta.desde(i.ultima),
            sugerencias_pendientes=i.pendientes,
        )
        for i in await caso.agentes(usuario)
    ]


@router.post(
    "/agentes/{agente}/ejecuciones",
    status_code=202,
    response_model=EjecucionCortaRespuesta,
    response_model_by_alias=True,
)
async def post_ejecucion(
    agente: str,
    usuario: Usuario = Depends(DIRECCION),  # noqa: B008
    caso: CasosAgentes = Depends(casos),  # noqa: B008
) -> EjecucionCortaRespuesta:
    try:
        ejecucion = await caso.ejecutar_ahora(usuario, agente)
    except AgenteInvalido as error:
        raise ErrorApi(400, "AGENTE_INVALIDO", "Agente inválido", "Ese agente no existe.") from error
    except EjecucionEnCurso as error:
        raise ErrorApi(409, "EJECUCION_EN_CURSO", "Ejecución en curso", "El agente ya se está ejecutando.") from error
    return EjecucionCortaRespuesta.desde(ejecucion)


@router.get("/ejecuciones", response_model=list[EjecucionCortaRespuesta], response_model_by_alias=True)
async def get_ejecuciones(
    agente: str | None = None,
    estado: str | None = None,
    limite: int = Query(default=20, ge=1, le=50),
    _: Usuario = Depends(DIRECCION),  # noqa: B008
    caso: CasosAgentes = Depends(casos),  # noqa: B008
) -> list[EjecucionCortaRespuesta]:
    _validar_agente(agente)
    if estado is not None and estado not in ESTADOS_EJECUCION:
        raise _datos_invalidos("estado", "El estado debe ser EN_CURSO, TERMINADA o FALLIDA.")
    return [EjecucionCortaRespuesta.desde(e) for e in await caso.ejecuciones(agente, estado, limite)]


@router.get("/ejecuciones/{ejecucion_id}", response_model=EjecucionRespuesta, response_model_by_alias=True)
async def get_ejecucion(
    ejecucion_id: str,
    _: Usuario = Depends(DIRECCION),  # noqa: B008
    caso: CasosAgentes = Depends(casos),  # noqa: B008
) -> EjecucionRespuesta:
    try:
        return EjecucionRespuesta.desde(await caso.ejecucion(ejecucion_id))
    except EjecucionNoEncontrada as error:
        raise ErrorApi(404, "EJECUCION_NO_ENCONTRADA", "Ejecución no encontrada", "La ejecución no existe.") from error


@router.get("/hallazgos", response_model=list[HallazgoVigenteRespuesta], response_model_by_alias=True)
async def get_hallazgos(
    agente: str | None = None,
    obra_id: str | None = Query(default=None, alias="obraId"),
    usuario: Usuario = Depends(LECTORES),  # noqa: B008
    caso: CasosAgentes = Depends(casos),  # noqa: B008
) -> list[HallazgoVigenteRespuesta]:
    _validar_agente(agente)
    return [HallazgoVigenteRespuesta.desde(v) for v in await caso.hallazgos(usuario, agente, obra_id)]


@router.get("/sugerencias", response_model=list[SugerenciaRespuesta], response_model_by_alias=True)
async def get_sugerencias(
    estado: str | None = None,
    agente: str | None = None,
    obra_id: str | None = Query(default=None, alias="obraId"),
    limite: int = Query(default=50, ge=1, le=100),
    usuario: Usuario = Depends(LECTORES),  # noqa: B008
    caso: CasosAgentes = Depends(casos),  # noqa: B008
) -> list[SugerenciaRespuesta]:
    _validar_agente(agente)
    if estado is not None and estado not in ESTADOS_SUGERENCIA:
        raise _datos_invalidos("estado", "El estado debe ser PENDIENTE, APROBADA o RECHAZADA.")
    return [SugerenciaRespuesta.desde(s) for s in await caso.sugerencias(usuario, estado, agente, obra_id, limite)]


@router.post("/sugerencias/{sugerencia_id}/aprobar", response_model=SugerenciaRespuesta, response_model_by_alias=True)
async def post_aprobar(
    sugerencia_id: str,
    request: Request,
    usuario: Usuario = Depends(LECTORES),  # noqa: B008
    caso: CasosAgentes = Depends(casos),  # noqa: B008
) -> SugerenciaRespuesta:
    cuerpo = await _cuerpo(request, CuerpoAprobar)
    return await _decidir(lambda: caso.aprobar(usuario, sugerencia_id, cuerpo.comentario), "COMENTARIO_INVALIDO")


@router.post("/sugerencias/{sugerencia_id}/rechazar", response_model=SugerenciaRespuesta, response_model_by_alias=True)
async def post_rechazar(
    sugerencia_id: str,
    request: Request,
    usuario: Usuario = Depends(LECTORES),  # noqa: B008
    caso: CasosAgentes = Depends(casos),  # noqa: B008
) -> SugerenciaRespuesta:
    cuerpo = await _cuerpo(request, CuerpoRechazar)
    return await _decidir(lambda: caso.rechazar(usuario, sugerencia_id, cuerpo.motivo), "MOTIVO_INVALIDO")


async def _decidir(accion: Any, codigo_invalido: str) -> SugerenciaRespuesta:
    try:
        return SugerenciaRespuesta.desde(await accion())
    except SugerenciaNoEncontrada as error:
        raise ErrorApi(
            404, "SUGERENCIA_NO_ENCONTRADA", "Sugerencia no encontrada", "La sugerencia no existe o no la puedes ver."
        ) from error
    except TextoInvalido as error:
        raise ErrorApi(
            422, codigo_invalido, "Datos inválidos", error.mensaje, errores=[ErrorCampo(error.campo, error.mensaje)]
        ) from error
    except SugerenciaYaDecidida as error:
        raise ErrorApi(
            409, "SUGERENCIA_YA_DECIDIDA", "Sugerencia ya decidida", "La sugerencia ya fue aprobada o rechazada."
        ) from error


async def _cuerpo[M: BaseModel](request: Request, modelo: type[M]) -> M:
    """Cuerpo opcional (sin cuerpo = sin comentario o sin motivo)."""
    try:
        crudo = await request.body()
        return modelo.model_validate(json.loads(crudo) if crudo.strip() else {})
    except (ValueError, ValidationError) as error:
        raise _datos_invalidos("cuerpo", "El cuerpo debe ser un objeto JSON con textos.") from error


def _validar_agente(agente: str | None) -> None:
    if agente is not None and agente not in AGENTES:
        raise _datos_invalidos("agente", "El agente debe ser SEGUIMIENTO_OBRAS, CONTROL_MATERIALES o CONTROL_COBROS.")


def _datos_invalidos(campo: str, mensaje: str) -> ErrorApi:
    error = error_generico(400, mensaje)
    error.errores = [ErrorCampo(campo=campo, mensaje=mensaje)]
    return error
