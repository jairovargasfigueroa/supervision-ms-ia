"""Rutas REST del constructor de reportes (503): solo `admin` y `gerente` (los reportes cruzan todas las obras).

- `POST /consultas-reporte` · texto o atajo (el gateway agrega `directorio.clientes`)
- `GET /consultas-reporte?limite=` · las del usuario del token
- `GET /catalogo-reportes` · el catálogo del modo REST
"""

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from ia.auth.dependencias import exigir_roles
from ia.auth.usuario import Usuario
from ia.comun.errores import ErrorApi, ErrorCampo, error_generico
from ia.comun.tiempo import Reloj, RelojSistema
from ia.constructor_reportes.aplicacion.consultar import (
    ConsultarReporte,
    DemasiadasConsultas,
    PedidoReporte,
    ReporteConsultado,
    TiempoAgotado,
)
from ia.constructor_reportes.aplicacion.motor import Directorio, MotorReportes
from ia.constructor_reportes.aplicacion.puertos import ReportesNoDisponibles, ServicioNoDisponible
from ia.constructor_reportes.dominio.atajos import ATAJOS, POR_CODIGO
from ia.constructor_reportes.dominio.catalogo import CATALOGO, REST
from ia.constructor_reportes.dominio.validacion import IntencionNoReconocida, RecetaInvalida
from ia.constructor_reportes.infraestructura.atipicos_mongo import ConsultaAtipicosMongo
from ia.constructor_reportes.infraestructura.consultas_mongo import RepositorioConsultasMongo
from ia.constructor_reportes.infraestructura.ejecutor_sql import EjecutorReportes, EjecutorSql
from ia.constructor_reportes.infraestructura.limite_mongo import LimitadorConsultasMongo
from ia.lenguaje.aplicacion.puerto import LlmNoDisponible

router = APIRouter(tags=["reportes"])
PERSONAS_REPORTES = exigir_roles("admin", "gerente")

TEXTO_MINIMO = 3
TEXTO_MAXIMO = 500
CLIENTES_MAXIMOS = 1000


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ClienteDirectorio(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    nombre: str = Field(max_length=300)


class DirectorioPedido(BaseModel):
    clientes: list[ClienteDirectorio] = Field(default_factory=list, max_length=CLIENTES_MAXIMOS)


class PedidoConsulta(BaseModel):
    texto: str | None = None
    atajo: str | None = None
    directorio: DirectorioPedido | None = None


class ColumnaRespuesta(_Camel):
    clave: str
    titulo: str
    tipo: str


class AplicadoRespuesta(_Camel):
    nombre: str
    valor: str


class ConsultaReporteRespuesta(_Camel):
    id: str
    texto: str | None
    atajo: str | None
    conjunto: str
    titulo_conjunto: str
    receta: dict[str, Any]
    aplicado: list[AplicadoRespuesta]
    resumen: str
    resumen_plantilla: bool
    avisos: list[str]
    columnas: list[ColumnaRespuesta]
    filas: list[list[str | None]]
    total_filas: int
    truncado: bool
    creada_en: datetime

    @classmethod
    def desde(cls, r: ReporteConsultado) -> "ConsultaReporteRespuesta":
        return cls(
            id=r.id,
            texto=r.texto,
            atajo=r.atajo,
            conjunto=r.conjunto,
            titulo_conjunto=r.titulo_conjunto,
            receta=r.receta,
            aplicado=[AplicadoRespuesta(nombre=e.nombre, valor=e.valor) for e in r.aplicado],
            resumen=r.resumen,
            resumen_plantilla=r.resumen_plantilla,
            avisos=r.resultado.avisos,
            columnas=[ColumnaRespuesta(clave=c.clave, titulo=c.titulo, tipo=c.tipo) for c in r.resultado.columnas],
            filas=r.resultado.filas,
            total_filas=r.resultado.total_filas,
            truncado=r.resultado.truncado,
            creada_en=r.creada_en,
        )


class ConsultaCortaRespuesta(_Camel):
    id: str
    texto: str | None
    atajo: str | None
    conjunto: str | None
    resultado: str
    resumen: str | None
    total_filas: int
    creada_en: datetime


class ConjuntoRespuesta(_Camel):
    conjunto: str
    titulo: str
    descripcion: str
    columnas: list[ColumnaRespuesta]
    filtros: list[str]
    agrupaciones: list[str]
    ejemplos: list[str]


class AtajoRespuesta(_Camel):
    atajo: str
    titulo: str
    descripcion: str


class CatalogoRespuesta(_Camel):
    conjuntos: list[ConjuntoRespuesta]
    atajos: list[AtajoRespuesta]


def _reloj(request: Request) -> Reloj:
    reloj: Reloj | None = getattr(request.app.state, "reloj", None)
    return reloj or RelojSistema(request.app.state.config.zona_horaria)


def crear_motor_reportes(request: Request) -> MotorReportes:
    """El mismo motor que usa la herramienta MCP `consultar_reporte` (501)."""
    estado = request.app.state
    sql = EjecutorSql(estado.motor_lector)
    ejecutor = EjecutorReportes(sql, ConsultaAtipicosMongo(estado.ia_db, sql, estado.config.zona_horaria))
    return MotorReportes(ejecutor, estado.catalogo_materiales)


def consultar_reporte(request: Request) -> ConsultarReporte:
    estado = request.app.state
    return ConsultarReporte(
        estado.modelo_lenguaje,
        crear_motor_reportes(request),
        RepositorioConsultasMongo(estado.ia_db),
        LimitadorConsultasMongo(estado.ia_db),
        _reloj(request),
    )


@router.post("/consultas-reporte", response_model=ConsultaReporteRespuesta, response_model_by_alias=True)
async def post_consulta_reporte(
    request: Request,
    usuario: Usuario = Depends(PERSONAS_REPORTES),  # noqa: B008
    caso: ConsultarReporte = Depends(consultar_reporte),  # noqa: B008
) -> ConsultaReporteRespuesta:
    # El límite se cuenta antes de validar el cuerpo.
    try:
        await caso.contar(usuario.id)
    except DemasiadasConsultas as error:
        raise ErrorApi(
            429,
            "DEMASIADAS_CONSULTAS",
            "Demasiadas consultas",
            "Se permiten 10 consultas de reportes por minuto.",
            encabezados={"Retry-After": str(error.segundos)},
        ) from error
    except ReportesNoDisponibles as error:
        raise _no_disponible("REPORTES_NO_DISPONIBLES") from error

    pedido = await _leer_pedido(request)
    token = request.headers.get("authorization", "").partition(" ")[2].strip()
    directorio = None if pedido.directorio is None else Directorio({c.id: c.nombre for c in pedido.directorio.clientes})
    try:
        reporte = await caso.consultar(PedidoReporte(usuario.id, pedido.texto, pedido.atajo, directorio, token))
    except IntencionNoReconocida as error:
        raise ErrorApi(
            422,
            "INTENCION_NO_RECONOCIDA",
            "No encontré un reporte para tu pedido",
            "El pedido no corresponde a ningún conjunto del catálogo.",
        ) from error
    except RecetaInvalida as error:
        raise ErrorApi(
            422,
            "RECETA_INVALIDA",
            "No encontré un reporte para tu pedido",
            "La receta no cumple el catálogo.",
            errores=[ErrorCampo(e.campo, e.mensaje) for e in error.errores],
        ) from error
    except LlmNoDisponible as error:
        raise _no_disponible("LLM_NO_DISPONIBLE") from error
    except ReportesNoDisponibles as error:
        raise _no_disponible("REPORTES_NO_DISPONIBLES") from error
    except ServicioNoDisponible as error:
        raise _no_disponible("SERVICIO_NO_DISPONIBLE", str(error)) from error
    except TiempoAgotado as error:
        raise ErrorApi(504, "TIEMPO_AGOTADO", "Tiempo agotado", "El reporte tardó demasiado.") from error
    return ConsultaReporteRespuesta.desde(reporte)


@router.get("/consultas-reporte", response_model=list[ConsultaCortaRespuesta], response_model_by_alias=True)
async def get_consultas_reporte(
    request: Request,
    limite: int = Query(default=10, ge=1, le=20),
    usuario: Usuario = Depends(PERSONAS_REPORTES),  # noqa: B008
) -> list[ConsultaCortaRespuesta]:
    try:
        ultimas = await RepositorioConsultasMongo(request.app.state.ia_db).ultimas(usuario.id, limite)
    except ReportesNoDisponibles as error:
        raise _no_disponible("REPORTES_NO_DISPONIBLES") from error
    return [
        ConsultaCortaRespuesta(
            id=c.id,
            texto=c.texto,
            atajo=c.atajo,
            conjunto=c.conjunto,
            resultado=c.resultado,
            resumen=c.resumen,
            total_filas=c.total_filas,
            creada_en=c.creada_en,
        )
        for c in ultimas
    ]


@router.get("/catalogo-reportes", response_model=CatalogoRespuesta, response_model_by_alias=True)
async def get_catalogo_reportes(_: Usuario = Depends(PERSONAS_REPORTES)) -> CatalogoRespuesta:  # noqa: B008
    return CatalogoRespuesta(
        conjuntos=[
            ConjuntoRespuesta(
                conjunto=c.conjunto,
                titulo=c.titulo,
                descripcion=c.descripcion,
                columnas=[ColumnaRespuesta(clave=x.clave, titulo=x.titulo, tipo=x.tipo) for x in c.columnas],
                filtros=[f.clave for f in c.filtros_del_modo(REST)],
                agrupaciones=list(c.dimensiones),
                ejemplos=list(c.ejemplos),
            )
            for c in CATALOGO.values()
        ],
        atajos=[AtajoRespuesta(atajo=a.atajo, titulo=a.titulo, descripcion=a.descripcion) for a in ATAJOS],
    )


async def _leer_pedido(request: Request) -> PedidoConsulta:
    try:
        cuerpo = json.loads(await request.body() or b"null")
        pedido = PedidoConsulta.model_validate(cuerpo)
    except (ValueError, ValidationError) as error:
        raise _datos_invalidos("cuerpo", "El cuerpo debe ser {texto} o {atajo}, con un directorio válido.") from error
    if (pedido.texto is None) == (pedido.atajo is None):
        raise _datos_invalidos("cuerpo", "Envía exactamente uno de texto o atajo.")
    if pedido.atajo is not None and pedido.atajo not in POR_CODIGO:
        raise _datos_invalidos("atajo", "Ese reporte común no existe.")
    if pedido.texto is not None:
        texto = pedido.texto.strip()
        if not TEXTO_MINIMO <= len(texto) <= TEXTO_MAXIMO:
            raise ErrorApi(
                400,
                "TEXTO_INVALIDO",
                "Texto inválido",
                "El pedido debe tener entre 3 y 500 caracteres.",
                errores=[ErrorCampo("texto", "El pedido debe tener entre 3 y 500 caracteres.")],
            )
        pedido = pedido.model_copy(update={"texto": texto})
    return pedido


def _datos_invalidos(campo: str, mensaje: str) -> ErrorApi:
    error = error_generico(400, mensaje)
    error.errores = [ErrorCampo(campo=campo, mensaje=mensaje)]
    return error


def _no_disponible(codigo: str, detalle: str | None = None) -> ErrorApi:
    return ErrorApi(
        503, codigo, "El servicio de reportes no está disponible", detalle or "Intenta de nuevo en un momento."
    )
