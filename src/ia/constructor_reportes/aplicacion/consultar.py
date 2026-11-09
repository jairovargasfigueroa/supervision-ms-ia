"""`ConsultarReporte`: el caso de uso de las personas (503), con conversación (505).

Cada turno (505, «Flujo de un turno»), todo dentro de 50 s:
1. Límite por minuto (antes de validar el cuerpo; lo llama la ruta primero).
2. Conversación nueva (sin `conversacionId` o con un atajo) o la del usuario (`ConversacionNoEncontrada` si no existe,
   expiró o es de otro).
3. Receta: atajo (sin modelo); respuesta a una duda del código (sin modelo); caché de recetas (primer turno); o el
   modelo, que devuelve RECETA, ACLARAR o NINGUNO, con autocorrección hasta 3 intentos.
4. Validación contra el catálogo. 5. Dudas del código (nombres ambiguos o inexistentes) → pregunta con opciones.
6. Motor (nombres → ids, consulta parametrizada, ids → nombres) y resumen (modelo con plantilla de respaldo).
7. Guarda `consulta_reporte` (también si no se reconoció o falló), la conversación y la caché.
"""

import asyncio
import hashlib
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ia.comun.tiempo import Reloj
from ia.constructor_reportes.aplicacion.interpretar import (
    Correccion,
    InterpretarPedido,
    RedactarResumen,
    SalidaNoValidaInterpretacion,
)
from ia.constructor_reportes.aplicacion.motor import VARIOS, Directorio, DudaNombre, MotorReportes
from ia.constructor_reportes.aplicacion.puertos import (
    CacheRecetas,
    ConsultaGuardada,
    LimitadorConsultas,
    ReportesNoDisponibles,
    RepositorioConsultas,
    RepositorioConversaciones,
    ServicioNoDisponible,
)
from ia.constructor_reportes.dominio.atajos import POR_CODIGO
from ia.constructor_reportes.dominio.catalogo import REST
from ia.constructor_reportes.dominio.conversacion import (
    ACLARACION,
    ASISTENTE,
    NO_RECONOCIDO,
    OPCION_NINGUNO,
    OPCION_TODOS,
    PEDIDO,
    REPORTE,
    USUARIO,
    Aclaracion,
    Conversacion,
    Duda,
    con_termino_elegido,
    elegida,
    normalizar,
)
from ia.constructor_reportes.dominio.resultado import ElementoAplicado, Resultado, describir_aplicado
from ia.constructor_reportes.dominio.validacion import (
    IntencionNoReconocida,
    RecetaInvalida,
    RecetaValidada,
    validar_receta,
)
from ia.lenguaje.aplicacion.puerto import (
    LlmNoDisponible,
    LlmTiempoAgotado,
    ModeloLenguaje,
    SalidaNoValida,
    registrar_validacion,
)

registro = logging.getLogger("ia.constructor_reportes")

LIMITE_POR_MINUTO = 20
TIEMPO_MAXIMO_S = 50.0
INTENTOS_RECETA = 3
HORAS_CACHE = 24
PLURALES = {"material": "materiales", "obra": "obras", "cliente": "clientes"}
SINGULARES = {"material": "el material", "obra": "la obra", "cliente": "el cliente"}


class DemasiadasConsultas(Exception):
    def __init__(self, segundos: int) -> None:
        super().__init__("Demasiadas consultas")
        self.segundos = segundos


class TiempoAgotado(Exception):
    """Más de 50 s dentro de IA."""


class ConversacionNoEncontrada(Exception):
    """505: la conversación no existe, expiró (30 min sin uso) o es de otro usuario."""


@dataclass(frozen=True)
class PedidoReporte:
    usuario_id: str
    texto: str | None
    atajo: str | None
    directorio: Directorio | None
    token: str
    conversacion_id: str | None = None


@dataclass(frozen=True)
class ReporteConsultado:
    id: str
    texto: str | None
    atajo: str | None
    conjunto: str
    titulo_conjunto: str
    receta: dict[str, Any]
    aplicado: list[ElementoAplicado]
    resumen: str
    resumen_plantilla: bool
    resultado: Resultado
    creada_en: datetime


@dataclass(frozen=True)
class RespuestaConsulta:
    """505: un reporte o una pregunta de aclaración, con la conversación a la que pertenece."""

    conversacion_id: str
    tipo: str
    reporte: ReporteConsultado | None = None
    aclaracion: Aclaracion | None = None


class ConversacionesEnMemoria:
    """Repositorio de conversaciones en memoria (pruebas y arranque sin `ia_db`)."""

    def __init__(self) -> None:
        self._por_id: dict[str, Conversacion] = {}

    async def obtener(self, conversacion_id: str) -> Conversacion | None:
        return self._por_id.get(conversacion_id)

    async def guardar(self, conversacion: Conversacion) -> None:
        self._por_id[conversacion.id] = conversacion


class ConsultarReporte:
    def __init__(
        self,
        modelo: ModeloLenguaje,
        motor: MotorReportes,
        consultas: RepositorioConsultas,
        limitador: LimitadorConsultas,
        reloj: Reloj,
        tiempo_maximo_s: float = TIEMPO_MAXIMO_S,
        *,
        conversaciones: RepositorioConversaciones | None = None,
        cache: CacheRecetas | None = None,
        limite_por_minuto: int = LIMITE_POR_MINUTO,
    ) -> None:
        self._modelo = modelo
        self._motor = motor
        self._consultas = consultas
        self._limitador = limitador
        self._reloj = reloj
        self._tiempo_maximo_s = tiempo_maximo_s
        self._conversaciones: RepositorioConversaciones = conversaciones or ConversacionesEnMemoria()
        self._cache = cache
        self._limite_por_minuto = limite_por_minuto

    @property
    def limite_por_minuto(self) -> int:
        return self._limite_por_minuto

    async def contar(self, usuario_id: str) -> None:
        """Paso 1. `DemasiadasConsultas` si pasa del límite en el minuto (cada turno y cada atajo cuentan)."""
        cuenta, segundos = await self._limitador.contar(usuario_id, self._reloj.ahora())
        if cuenta > self._limite_por_minuto:
            raise DemasiadasConsultas(segundos)

    async def consultar(self, pedido: PedidoReporte) -> RespuestaConsulta:
        consulta_id = str(uuid.uuid4())
        creada_en = self._reloj.ahora()
        inicio = time.monotonic()
        estado: dict[str, Any] = {"conjunto": None, "receta": None, "conversacion_id": None}
        try:
            async with asyncio.timeout(self._tiempo_maximo_s):
                respuesta = await self._consultar(pedido, consulta_id, creada_en, estado)
        except TimeoutError as error:
            await self._guardar_error(pedido, consulta_id, creada_en, inicio, estado, "ERROR", "TIEMPO_AGOTADO")
            raise TiempoAgotado() from error
        except IntencionNoReconocida:
            await self._guardar_error(pedido, consulta_id, creada_en, inicio, estado, "NO_RECONOCIDA", None)
            raise
        except RecetaInvalida:
            await self._guardar_error(pedido, consulta_id, creada_en, inicio, estado, "RECETA_INVALIDA", None)
            raise
        except LlmNoDisponible:
            await self._guardar_error(pedido, consulta_id, creada_en, inicio, estado, "ERROR", "LLM_NO_DISPONIBLE")
            raise
        except ReportesNoDisponibles:
            await self._guardar_error(
                pedido, consulta_id, creada_en, inicio, estado, "ERROR", "REPORTES_NO_DISPONIBLES"
            )
            raise
        except ServicioNoDisponible:
            await self._guardar_error(pedido, consulta_id, creada_en, inicio, estado, "ERROR", "SERVICIO_NO_DISPONIBLE")
            raise
        if respuesta.reporte is not None:
            await self._consultas.guardar(
                self._guardada(pedido, consulta_id, creada_en, inicio, estado, "OK", None, respuesta.reporte)
            )
        return respuesta

    # --- un turno ---------------------------------------------------------------------------------------------------

    async def _consultar(
        self, pedido: PedidoReporte, consulta_id: str, creada_en: datetime, estado: dict[str, Any]
    ) -> RespuestaConsulta:
        hoy = self._reloj.hoy()
        ahora = self._reloj.ahora()
        conversacion = await self._conversacion(pedido, ahora)
        estado["conversacion_id"] = conversacion.id
        atajo = POR_CODIGO[pedido.atajo] if pedido.atajo is not None else None
        conversacion.agregar(USUARIO, pedido.texto or (atajo.titulo if atajo else ""), PEDIDO, ahora)
        primer_turno = conversacion.es_nueva
        desde_modelo = False

        if atajo is not None:
            receta = validar_receta(atajo.receta(hoy), REST, hoy)
            titulo = atajo.titulo
        else:
            assert pedido.texto is not None
            receta_o_none = self._resolver_duda(conversacion, pedido.texto, hoy)
            if receta_o_none is None and primer_turno:
                receta_o_none = await self._de_cache(pedido.texto, hoy)
            if receta_o_none is None:
                interpretada = await self._interpretar(pedido.texto, hoy, conversacion, consulta_id, ahora)
                if isinstance(interpretada, Aclaracion):
                    await self._conversaciones.guardar(conversacion)
                    return RespuestaConsulta(conversacion.id, ACLARACION, aclaracion=interpretada)
                receta_o_none = interpretada
                desde_modelo = True
            receta = receta_o_none
            titulo = receta.catalogo.titulo
        estado["conjunto"] = receta.conjunto
        estado["receta"] = receta.como_json()

        if atajo is None and conversacion.puede_preguntar:
            duda = await self._motor.buscar_duda(
                receta, directorio=pedido.directorio, token=pedido.token, aceptados=conversacion.aceptados
            )
            if duda is not None:
                pregunta, opciones = _pregunta(duda)
                conversacion.duda = Duda(duda.campo, duda.termino, duda.clase, opciones, receta.como_json())
                aclaracion = conversacion.preguntar(pregunta, opciones, ahora)
                await self._conversaciones.guardar(conversacion)
                return RespuestaConsulta(conversacion.id, ACLARACION, aclaracion=aclaracion)

        resultado = await self._motor.ejecutar(receta, hoy, directorio=pedido.directorio, token=pedido.token)
        aplicado = describir_aplicado(receta)
        resumen, plantilla = await RedactarResumen(self._modelo).redactar(
            pedido.texto, titulo, receta.catalogo.titulo, aplicado, resultado, contexto=consulta_id
        )
        reporte = ReporteConsultado(
            id=consulta_id,
            texto=pedido.texto,
            atajo=pedido.atajo,
            conjunto=receta.conjunto,
            titulo_conjunto=receta.catalogo.titulo,
            receta=receta.como_json(),
            aplicado=aplicado,
            resumen=resumen,
            resumen_plantilla=plantilla,
            resultado=resultado,
            creada_en=creada_en,
        )
        conversacion.receta = receta.como_json()
        conversacion.duda = None
        entendido = "; ".join(f"{e.nombre}: {e.valor}" for e in aplicado) or "sin filtros"
        conversacion.agregar(ASISTENTE, f"Reporte «{titulo}» ({entendido}).", REPORTE, ahora)
        await self._conversaciones.guardar(conversacion)
        if desde_modelo and primer_turno and pedido.texto is not None:
            await self._a_cache(pedido.texto, hoy, receta, ahora)
        return RespuestaConsulta(conversacion.id, REPORTE, reporte=reporte)

    async def _conversacion(self, pedido: PedidoReporte, ahora: datetime) -> Conversacion:
        if pedido.atajo is not None or pedido.conversacion_id is None:
            return Conversacion(str(uuid.uuid4()), pedido.usuario_id, ahora, ahora)
        conversacion = await self._conversaciones.obtener(pedido.conversacion_id)
        if conversacion is None or conversacion.usuario_id != pedido.usuario_id or conversacion.expira_en <= ahora:
            raise ConversacionNoEncontrada(pedido.conversacion_id)
        return conversacion

    def _resolver_duda(self, conversacion: Conversacion, texto: str, hoy: date) -> RecetaValidada | None:
        """Respuesta a una pregunta del código, sin modelo: una opción (nombre exacto), «Todos» o «Ninguno». Otro
        texto deja la duda y sigue con el modelo (la pregunta está en los turnos)."""
        duda = conversacion.duda
        if duda is None:
            return None
        conversacion.duda = None
        opcion = elegida(duda, texto)
        if opcion is None:
            return None
        if opcion in (OPCION_TODOS, OPCION_NINGUNO):
            conversacion.aceptados.append(normalizar(duda.termino))
            datos = duda.receta
        else:
            datos = con_termino_elegido(duda.receta, duda.campo, duda.termino, opcion)
        try:
            return validar_receta(datos, REST, hoy)
        except (IntencionNoReconocida, RecetaInvalida):
            return None

    async def _interpretar(
        self, texto: str, hoy: date, conversacion: Conversacion, consulta_id: str, ahora: datetime
    ) -> RecetaValidada | Aclaracion:
        """El modelo con autocorrección: hasta `INTENTOS_RECETA` intentos, devolviéndole los errores."""
        correcciones: list[Correccion] = []
        for intento in range(1, INTENTOS_RECETA + 1):
            ultimo = intento == INTENTOS_RECETA
            try:
                interpretacion = await InterpretarPedido(self._modelo).interpretar(
                    texto,
                    hoy,
                    contexto=consulta_id,
                    receta_anterior=conversacion.receta,
                    turnos=conversacion.turnos_recientes(),
                    puede_preguntar=conversacion.puede_preguntar,
                    correcciones=correcciones,
                )
            except SalidaNoValida as error:
                registrar_validacion(contexto=consulta_id, operacion="receta", valida=False)
                if ultimo:
                    raise IntencionNoReconocida("El modelo no devolvió una receta.") from error
                correcciones.append(Correccion({"salida": "no era un objeto JSON"}, ["Responde con un objeto JSON."]))
                continue
            except SalidaNoValidaInterpretacion as error:
                registrar_validacion(contexto=consulta_id, operacion="receta", valida=False)
                if ultimo:
                    raise IntencionNoReconocida("El modelo no devolvió una receta.") from error
                correcciones.append(Correccion(error.datos, [str(error)]))
                continue
            except LlmTiempoAgotado as error:
                raise LlmNoDisponible("El modelo no respondió a tiempo.") from error

            if interpretacion.accion == "NINGUNO":
                registrar_validacion(contexto=consulta_id, operacion="receta", valida=True)
                conversacion.agregar(ASISTENTE, "No es un reporte posible con el catálogo.", NO_RECONOCIDO, ahora)
                await self._conversaciones.guardar(conversacion)
                raise IntencionNoReconocida("El pedido no corresponde a ningún conjunto del catálogo.")
            if interpretacion.accion == "ACLARAR":
                registrar_validacion(contexto=consulta_id, operacion="receta", valida=True)
                assert interpretacion.pregunta is not None
                return conversacion.preguntar(interpretacion.pregunta, interpretacion.opciones, ahora)
            try:
                receta = validar_receta(interpretacion.receta, REST, hoy)
            except RecetaInvalida as error:
                registrar_validacion(contexto=consulta_id, operacion="receta", valida=False)
                if ultimo:
                    raise
                correcciones.append(
                    Correccion(interpretacion.crudo, [f"{e.campo}: {e.mensaje}" for e in error.errores])
                )
                continue
            except IntencionNoReconocida as error:
                registrar_validacion(contexto=consulta_id, operacion="receta", valida=False)
                if ultimo:
                    raise
                correcciones.append(Correccion(interpretacion.crudo, [f"receta: {error}"]))
                continue
            registrar_validacion(contexto=consulta_id, operacion="receta", valida=True)
            return receta
        raise AssertionError("inalcanzable")  # pragma: no cover

    # --- caché de recetas -------------------------------------------------------------------------------------------

    @staticmethod
    def _clave_cache(texto: str, hoy: date) -> str:
        return hashlib.sha256(f"{normalizar(texto)}|{hoy.isoformat()}".encode()).hexdigest()

    async def _de_cache(self, texto: str, hoy: date) -> RecetaValidada | None:
        if self._cache is None:
            return None
        datos = await self._cache.obtener(self._clave_cache(texto, hoy))
        if datos is None:
            return None
        try:
            return validar_receta(datos, REST, hoy)
        except (IntencionNoReconocida, RecetaInvalida):
            return None

    async def _a_cache(self, texto: str, hoy: date, receta: RecetaValidada, ahora: datetime) -> None:
        if self._cache is not None:
            await self._cache.guardar(
                self._clave_cache(texto, hoy), receta.como_json(), ahora + timedelta(hours=HORAS_CACHE)
            )

    # --- guardar ----------------------------------------------------------------------------------------------------

    async def _guardar_error(
        self,
        pedido: PedidoReporte,
        consulta_id: str,
        creada_en: datetime,
        inicio: float,
        estado: dict[str, Any],
        resultado: str,
        codigo: str | None,
    ) -> None:
        try:
            await self._consultas.guardar(
                self._guardada(pedido, consulta_id, creada_en, inicio, estado, resultado, codigo, None)
            )
        except ReportesNoDisponibles:
            registro.warning("No se pudo guardar la consulta %s (ia_db no responde).", consulta_id)

    def _guardada(
        self,
        pedido: PedidoReporte,
        consulta_id: str,
        creada_en: datetime,
        inicio: float,
        estado: dict[str, Any],
        resultado: str,
        codigo: str | None,
        reporte: ReporteConsultado | None,
    ) -> ConsultaGuardada:
        reconocida = resultado in ("OK", "ERROR")
        return ConsultaGuardada(
            id=consulta_id,
            usuario_id=pedido.usuario_id,
            texto=pedido.texto,
            atajo=pedido.atajo,
            conjunto=estado["conjunto"] if reconocida else None,
            receta=estado["receta"] if reconocida else None,
            resumen=reporte.resumen if reporte else None,
            resumen_plantilla=reporte.resumen_plantilla if reporte else False,
            resultado=resultado,
            error_codigo=codigo,
            total_filas=reporte.resultado.total_filas if reporte else 0,
            proveedor_llm=self._modelo.proveedor,
            modelo=self._modelo.modelo,
            duracion_ms=int((time.monotonic() - inicio) * 1000),
            creada_en=creada_en,
            conversacion_id=estado.get("conversacion_id"),
        )


def _pregunta(duda: DudaNombre) -> tuple[str, tuple[str, ...]]:
    if duda.clase == VARIOS:
        return (f"«{duda.termino}» coincide con varios {PLURALES[duda.campo]}. ¿Cuál?", (*duda.nombres, OPCION_TODOS))
    return (
        f"No encontré {SINGULARES[duda.campo]} «{duda.termino}». ¿Quisiste decir…?",
        (*duda.nombres, OPCION_NINGUNO),
    )


__all__: Sequence[str] = (
    "ConsultarReporte",
    "ConversacionNoEncontrada",
    "DemasiadasConsultas",
    "PedidoReporte",
    "ReporteConsultado",
    "RespuestaConsulta",
    "TiempoAgotado",
)
