"""Reporte final de un agente (502): modelos Pydantic estrictos, JSON Schema por agente y validación adicional.

El esquema que recibe `generar_json` limita `tipo`, `accion` y `evidencia.herramienta` a los valores del agente que se
ejecuta; el código vuelve a validar todo (la salida del modelo nunca se usa sin validar, 504):

- forma (Pydantic, `extra="forbid"`): largos, severidad, máximo 10 hallazgos y 10 sugerencias, 1 a 5 evidencias;
- listas permitidas del agente (tipos, acciones, herramientas);
- cada `obraId` salió en algún resultado de herramienta de esta ejecución (A4);
- cada evidencia cita una herramienta llamada con éxito en esta ejecución;
- `hallazgo` es un índice válido y su `obraId` coincide;
- los textos quedan sin etiquetas `<…>` ni caracteres de control (A8). `obra_nombre` lo pone el código.
"""

import copy
from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from ia.agentes.dominio.definiciones import DefinicionAgente
from ia.agentes.dominio.ejecucion import Evidencia, HallazgoGuardado
from ia.seguridad.limpiar import texto_seguro

RESUMEN_MAXIMO = 600
TEXTO_MAXIMO = 300
DATO_MAXIMO = 200
OBRA_SIN_NOMBRE = "Obra sin nombre en los datos"


class _Estricto(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=False, frozen=True)


Texto = Annotated[str, Field(min_length=1, max_length=TEXTO_MAXIMO)]


class EvidenciaReporte(_Estricto):
    herramienta: Annotated[str, Field(min_length=1, max_length=60)]
    dato: Annotated[str, Field(min_length=1, max_length=DATO_MAXIMO)]


class HallazgoReporte(_Estricto):
    obra_id: UUID = Field(alias="obraId")
    tipo: Annotated[str, Field(min_length=1, max_length=60)]
    severidad: Literal["ALTA", "MEDIA", "BAJA"]
    situacion: Texto
    causa_probable: Texto = Field(alias="causaProbable", description='"No determinada" si no hay datos')
    evidencia: Annotated[list[EvidenciaReporte], Field(min_length=1, max_length=5)]


class SugerenciaReporte(_Estricto):
    obra_id: UUID = Field(alias="obraId")
    accion: Annotated[str, Field(min_length=1, max_length=60)]
    descripcion: Texto
    hallazgo: StrictInt = Field(ge=0, description="índice del hallazgo que la origina")


class ReporteAgente(_Estricto):
    resumen: Annotated[str, Field(min_length=1, max_length=RESUMEN_MAXIMO)]
    hallazgos: Annotated[list[HallazgoReporte], Field(max_length=10)]
    sugerencias: Annotated[list[SugerenciaReporte], Field(max_length=10)]


def esquema_reporte(agente: DefinicionAgente) -> dict[str, Any]:
    """JSON Schema del reporte con `tipo`, `accion` y `evidencia.herramienta` limitados a los valores del agente."""
    esquema = copy.deepcopy(ReporteAgente.model_json_schema(by_alias=True))
    definiciones = esquema["$defs"]
    definiciones["HallazgoReporte"]["properties"]["tipo"] = {"type": "string", "enum": list(agente.tipos)}
    definiciones["SugerenciaReporte"]["properties"]["accion"] = {"type": "string", "enum": list(agente.acciones)}
    definiciones["EvidenciaReporte"]["properties"]["herramienta"] = {
        "type": "string",
        "enum": list(agente.herramientas),
    }
    return esquema


@dataclass(frozen=True)
class SugerenciaValidada:
    obra_id: str
    obra_nombre: str
    accion: str
    descripcion: str
    hallazgo_indice: int


@dataclass(frozen=True)
class ReporteValidado:
    resumen: str
    hallazgos: tuple[HallazgoGuardado, ...]
    sugerencias: tuple[SugerenciaValidada, ...]


class ReporteInvalido(Exception):
    def __init__(self, errores: list[str]) -> None:
        super().__init__("; ".join(errores))
        self.errores = errores


def validar_reporte(
    datos: Any,
    agente: DefinicionAgente,
    obras_vistas: Mapping[str, str | None],
    herramientas_con_exito: Set[str],
) -> ReporteValidado:
    """`ReporteInvalido` con la lista de problemas (para el reintento) o el reporte listo para guardar.

    `obras_vistas`: obraId → nombre (o `None`) de las obras que salieron en los resultados de esta ejecución.
    """
    try:
        reporte = ReporteAgente.model_validate(datos)
    except ValidationError as error:
        raise ReporteInvalido(
            [f"{'.'.join(str(p) for p in e.get('loc', ())) or 'reporte'}: {e.get('msg', '')}" for e in error.errors()][
                :10
            ]
        ) from error

    errores: list[str] = []
    resumen = _limpio(reporte.resumen, RESUMEN_MAXIMO, "resumen", errores)
    hallazgos: list[HallazgoGuardado] = []
    for i, h in enumerate(reporte.hallazgos):
        ruta = f"hallazgos[{i}]"
        obra_id = str(h.obra_id)
        if h.tipo not in agente.tipos:
            errores.append(f"{ruta}.tipo: «{h.tipo[:60]}» no es un tipo de hallazgo de este agente.")
        if obra_id not in obras_vistas:
            errores.append(f"{ruta}.obraId: la obra no salió en los resultados de las herramientas.")
        evidencias: list[Evidencia] = []
        for j, e in enumerate(h.evidencia):
            if e.herramienta not in herramientas_con_exito:
                errores.append(
                    f"{ruta}.evidencia[{j}].herramienta: «{e.herramienta[:60]}» no se llamó con éxito en esta "
                    "ejecución."
                )
            evidencias.append(
                Evidencia(e.herramienta, _limpio(e.dato, DATO_MAXIMO, f"{ruta}.evidencia[{j}].dato", errores))
            )
        hallazgos.append(
            HallazgoGuardado(
                obra_id=obra_id,
                obra_nombre=obras_vistas.get(obra_id) or OBRA_SIN_NOMBRE,
                tipo=h.tipo,
                severidad=h.severidad,
                situacion=_limpio(h.situacion, TEXTO_MAXIMO, f"{ruta}.situacion", errores),
                causa_probable=_limpio(h.causa_probable, TEXTO_MAXIMO, f"{ruta}.causaProbable", errores),
                evidencia=tuple(evidencias),
            )
        )

    sugerencias: list[SugerenciaValidada] = []
    for i, s in enumerate(reporte.sugerencias):
        ruta = f"sugerencias[{i}]"
        obra_id = str(s.obra_id)
        if s.accion not in agente.acciones:
            errores.append(f"{ruta}.accion: «{s.accion[:60]}» no está en la lista permitida de este agente.")
        if obra_id not in obras_vistas:
            errores.append(f"{ruta}.obraId: la obra no salió en los resultados de las herramientas.")
        if s.hallazgo >= len(hallazgos):
            errores.append(f"{ruta}.hallazgo: no existe el hallazgo {s.hallazgo}.")
        elif hallazgos[s.hallazgo].obra_id != obra_id:
            errores.append(f"{ruta}.obraId: no coincide con la obra del hallazgo {s.hallazgo}.")
        sugerencias.append(
            SugerenciaValidada(
                obra_id=obra_id,
                obra_nombre=obras_vistas.get(obra_id) or OBRA_SIN_NOMBRE,
                accion=s.accion,
                descripcion=_limpio(s.descripcion, TEXTO_MAXIMO, f"{ruta}.descripcion", errores),
                hallazgo_indice=s.hallazgo,
            )
        )
    if errores:
        raise ReporteInvalido(errores)
    return ReporteValidado(resumen, tuple(hallazgos), tuple(sugerencias))


def _limpio(texto: str, maximo: int, ruta: str, errores: list[str]) -> str:
    limpio = texto_seguro(texto, maximo)
    if not limpio:
        errores.append(f"{ruta}: queda vacío sin etiquetas ni caracteres de control.")
    return limpio
