"""`ConsultarReporte`: el caso de uso de las personas (503).

1. Límite por minuto (antes de validar el cuerpo; lo llama la ruta primero).
2. Texto → receta (modelo de lenguaje) o atajo → receta fija (sin modelo).
3. Validación contra el catálogo. 4–6. Motor (nombres → ids, consulta parametrizada, ids → nombres).
7. Resumen (modelo con plantilla de respaldo). 8. Guarda `consulta_reporte`, también si no se reconoció o falló.
Todo dentro de 50 s.
"""

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ia.comun.tiempo import Reloj
from ia.constructor_reportes.aplicacion.interpretar import InterpretarPedido, RedactarResumen
from ia.constructor_reportes.aplicacion.motor import Directorio, MotorReportes
from ia.constructor_reportes.aplicacion.puertos import (
    ConsultaGuardada,
    LimitadorConsultas,
    ReportesNoDisponibles,
    RepositorioConsultas,
    ServicioNoDisponible,
)
from ia.constructor_reportes.dominio.atajos import POR_CODIGO
from ia.constructor_reportes.dominio.catalogo import REST
from ia.constructor_reportes.dominio.resultado import ElementoAplicado, Resultado, describir_aplicado
from ia.constructor_reportes.dominio.validacion import IntencionNoReconocida, RecetaInvalida, validar_receta
from ia.lenguaje.aplicacion.puerto import (
    LlmNoDisponible,
    LlmTiempoAgotado,
    ModeloLenguaje,
    SalidaNoValida,
    registrar_validacion,
)

registro = logging.getLogger("ia.constructor_reportes")

LIMITE_POR_MINUTO = 10
TIEMPO_MAXIMO_S = 50.0


class DemasiadasConsultas(Exception):
    def __init__(self, segundos: int) -> None:
        super().__init__("Demasiadas consultas")
        self.segundos = segundos


class TiempoAgotado(Exception):
    """Más de 50 s dentro de IA."""


@dataclass(frozen=True)
class PedidoReporte:
    usuario_id: str
    texto: str | None
    atajo: str | None
    directorio: Directorio | None
    token: str


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


class ConsultarReporte:
    def __init__(
        self,
        modelo: ModeloLenguaje,
        motor: MotorReportes,
        consultas: RepositorioConsultas,
        limitador: LimitadorConsultas,
        reloj: Reloj,
        tiempo_maximo_s: float = TIEMPO_MAXIMO_S,
    ) -> None:
        self._modelo = modelo
        self._motor = motor
        self._consultas = consultas
        self._limitador = limitador
        self._reloj = reloj
        self._tiempo_maximo_s = tiempo_maximo_s

    async def contar(self, usuario_id: str) -> None:
        """Paso 1. `DemasiadasConsultas` si pasa de 10 en el minuto (también con atajos)."""
        cuenta, segundos = await self._limitador.contar(usuario_id, self._reloj.ahora())
        if cuenta > LIMITE_POR_MINUTO:
            raise DemasiadasConsultas(segundos)

    async def consultar(self, pedido: PedidoReporte) -> ReporteConsultado:
        consulta_id = str(uuid.uuid4())
        creada_en = self._reloj.ahora()
        inicio = time.monotonic()
        estado: dict[str, Any] = {"conjunto": None, "receta": None}
        try:
            async with asyncio.timeout(self._tiempo_maximo_s):
                reporte = await self._consultar(pedido, consulta_id, creada_en, estado)
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
        await self._consultas.guardar(
            self._guardada(pedido, consulta_id, creada_en, inicio, estado, "OK", None, reporte)
        )
        return reporte

    async def _consultar(
        self, pedido: PedidoReporte, consulta_id: str, creada_en: datetime, estado: dict[str, Any]
    ) -> ReporteConsultado:
        hoy = self._reloj.hoy()
        if pedido.atajo is not None:
            atajo = POR_CODIGO[pedido.atajo]
            receta = validar_receta(atajo.receta(hoy), REST, hoy)
            titulo = atajo.titulo
        else:
            assert pedido.texto is not None
            try:
                datos = await InterpretarPedido(self._modelo).interpretar(pedido.texto, hoy, contexto=consulta_id)
            except SalidaNoValida as error:
                registrar_validacion(contexto=consulta_id, operacion="receta", valida=False)
                raise IntencionNoReconocida("El modelo no devolvió una receta.") from error
            except LlmTiempoAgotado as error:
                raise LlmNoDisponible("El modelo no respondió a tiempo.") from error
            try:
                receta = validar_receta(datos, REST, hoy)
            except (IntencionNoReconocida, RecetaInvalida):
                registrar_validacion(contexto=consulta_id, operacion="receta", valida=False)
                raise
            registrar_validacion(contexto=consulta_id, operacion="receta", valida=True)
            titulo = receta.catalogo.titulo
        estado["conjunto"] = receta.conjunto
        estado["receta"] = receta.como_json()

        resultado = await self._motor.ejecutar(receta, hoy, directorio=pedido.directorio, token=pedido.token)
        aplicado = describir_aplicado(receta)
        resumen, plantilla = await RedactarResumen(self._modelo).redactar(
            pedido.texto, titulo, receta.catalogo.titulo, aplicado, resultado, contexto=consulta_id
        )
        return ReporteConsultado(
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
        )
