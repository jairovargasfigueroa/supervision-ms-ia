"""Adaptador de entrada de eventos: traduce `datos` de los 6 eventos a lecturas y registra sus manejadores.

Lector tolerante: los campos desconocidos se ignoran; `ubicacion` y `zona` (`ObraRegistrada`) y `precioReferencial`
(`MovimientoRegistrado`) pueden faltar. Un campo obligatorio que falta o no tiene el formato → `MensajeInvalidoError`
(directo a `plataforma.fallidos`).
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ia.comun.tiempo import Reloj, fecha_en_zona, leer_instante
from ia.mensajeria.consumidor import ConsumidorMensajes
from ia.mensajeria.inbox import manejador_con_inbox
from ia.mensajeria.sobre import MensajeInvalidoError, Sobre
from ia.reportes.aplicacion.lecturas import (
    CobroEmitidoLeido,
    MovimientoLeido,
    ObraLeida,
    PlanillaAprobadaLeida,
    PlanillaObservadaLeida,
)
from ia.reportes.aplicacion.proyecciones import Proyecciones
from ia.reportes.dominio.valores import SALIDA, TIPOS_MOVIMIENTO
from ia.reportes.infraestructura.repositorio_sql import RepositorioReportesSql

OBRA_REGISTRADA = "evento.obras.ObraRegistrada"
CONTRATO_REGISTRADO = "evento.finanzas.ContratoRegistrado"
PLANILLA_APROBADA = "evento.obras.PlanillaAprobada"
PLANILLA_OBSERVADA = "evento.obras.PlanillaObservada"
COBRO_EMITIDO = "evento.finanzas.CobroEmitido"
MOVIMIENTO_REGISTRADO = "evento.materiales.MovimientoRegistrado"


def registrar_manejadores(
    consumidor: ConsumidorMensajes, motor: AsyncEngine, reloj: Reloj, zona_horaria: str = "America/La_Paz"
) -> Proyecciones:
    """Un manejador por evento: inbox + proyección en la misma transacción."""
    proyecciones = Proyecciones(reloj)

    def con_repositorio(
        aplicar: Callable[[Sobre, RepositorioReportesSql], Awaitable[object]],
    ) -> Callable[[Sobre, AsyncConnection], Awaitable[None]]:
        async def en_transaccion(sobre: Sobre, conexion: AsyncConnection) -> None:
            await aplicar(sobre, RepositorioReportesSql(conexion))

        return en_transaccion

    async def obra(sobre: Sobre, repo: RepositorioReportesSql) -> None:
        await proyecciones.obra_registrada(repo, leer_obra_registrada(sobre.datos))

    async def contrato(sobre: Sobre, repo: RepositorioReportesSql) -> None:
        await proyecciones.contrato_registrado(repo, _uuid(sobre.datos, "obraId"))

    async def aprobada(sobre: Sobre, repo: RepositorioReportesSql) -> None:
        await proyecciones.planilla_aprobada(repo, leer_planilla_aprobada(sobre))

    async def observada(sobre: Sobre, repo: RepositorioReportesSql) -> None:
        await proyecciones.planilla_observada(repo, leer_planilla_observada(sobre))

    async def cobro(sobre: Sobre, repo: RepositorioReportesSql) -> None:
        await proyecciones.cobro_emitido(repo, leer_cobro_emitido(sobre, zona_horaria))

    async def movimiento(sobre: Sobre, repo: RepositorioReportesSql) -> None:
        await proyecciones.movimiento_registrado(repo, leer_movimiento(sobre.datos))

    for clave, aplicar in (
        (OBRA_REGISTRADA, obra),
        (CONTRATO_REGISTRADO, contrato),
        (PLANILLA_APROBADA, aprobada),
        (PLANILLA_OBSERVADA, observada),
        (COBRO_EMITIDO, cobro),
        (MOVIMIENTO_REGISTRADO, movimiento),
    ):
        consumidor.registrar(clave, manejador_con_inbox(motor, con_repositorio(aplicar), reloj))
    return proyecciones


# Traductores (también los usa la carga inicial para los mismos campos).


def leer_obra_registrada(datos: dict[str, Any]) -> ObraLeida:
    asignaciones = datos.get("asignaciones")
    if not isinstance(asignaciones, dict):
        raise MensajeInvalidoError("Faltan las asignaciones de la obra.")
    contrato = datos.get("contrato")
    if not isinstance(contrato, dict):
        raise MensajeInvalidoError("Falta el contrato de la obra.")
    return ObraLeida(
        obra_id=_uuid(datos, "obraId"),
        nombre=_texto(datos, "nombre"),
        ubicacion=_texto_opcional(datos, "ubicacion"),
        zona=_texto_opcional(datos, "zona"),
        cliente_id=_uuid_opcional(asignaciones, "clienteId"),
        residente_id=_uuid_opcional(asignaciones, "residenteId"),
        maestro_id=_uuid_opcional(asignaciones, "maestroId"),
        almacenero_id=_uuid_opcional(asignaciones, "almaceneroId"),
        fecha_inicio=_fecha(datos, "fechaInicio"),
        plazo_dias=_entero(contrato, "plazoDias"),
        monto_contrato=_decimal(contrato, "monto"),
    )


def leer_planilla_aprobada(sobre: Sobre) -> PlanillaAprobadaLeida:
    datos = sobre.datos
    lineas = datos.get("lineas")
    if not isinstance(lineas, list) or not all(isinstance(linea, dict) for linea in lineas):
        raise MensajeInvalidoError("Faltan las líneas de la planilla.")
    return PlanillaAprobadaLeida(
        planilla_id=_uuid(datos, "planillaId"),
        obra_id=_uuid(datos, "obraId"),
        periodo=_periodo(datos, "periodo"),
        neto=_decimal(datos, "neto"),
        cobro_id=_uuid_opcional(datos, "cobroId"),
        montos_lineas=tuple(_decimal(linea, "monto") for linea in lineas),
        aprobada_en=sobre.ocurrido_en,
    )


def leer_planilla_observada(sobre: Sobre) -> PlanillaObservadaLeida:
    datos = sobre.datos
    return PlanillaObservadaLeida(
        planilla_id=_uuid(datos, "planillaId"),
        obra_id=_uuid(datos, "obraId"),
        motivo=_texto_opcional(datos, "motivo"),
        origen=_texto_opcional(datos, "origen"),
        ocurrida_en=sobre.ocurrido_en,
    )


def leer_cobro_emitido(sobre: Sobre, zona_horaria: str) -> CobroEmitidoLeido:
    datos = sobre.datos
    return CobroEmitidoLeido(
        cobro_id=_uuid(datos, "cobroId"),
        planilla_id=_uuid(datos, "planillaId"),
        numero=_texto(datos, "numero"),
        vence_en=_fecha(datos, "venceEn"),
        emitido_en=fecha_en_zona(sobre.ocurrido_en, zona_horaria) if sobre.ocurrido_en else None,
    )


def leer_movimiento(datos: dict[str, Any], id_campo: str = "movimientoId") -> MovimientoLeido:
    """`MovimientoRegistrado` (`movimientoId`) o un elemento de `GET /obras/{id}/movimientos` (`id`, sin obra)."""
    tipo = _texto(datos, "tipo")
    if tipo not in TIPOS_MOVIMIENTO:
        raise MensajeInvalidoError(f"Tipo de movimiento desconocido: {tipo}.")
    partida_id = _uuid_opcional(datos, "partidaId")
    if tipo == SALIDA and partida_id is None:
        raise MensajeInvalidoError("Una SALIDA necesita partidaId.")
    supera = datos.get("superaStock", False)
    if not isinstance(supera, bool):
        raise MensajeInvalidoError("superaStock no es booleano.")
    precio = datos.get("precioReferencial")
    return MovimientoLeido(
        movimiento_id=_uuid(datos, id_campo),
        obra_id=_uuid(datos, "obraId"),
        material_id=_texto(datos, "materialId"),
        tipo=tipo,
        cantidad=_decimal(datos, "cantidad"),
        partida_id=partida_id,
        periodo=_periodo(datos, "periodo"),
        supera_stock=supera,
        registrado_en=_instante(datos, "registradoEn"),
        precio_referencial=None if precio is None else _decimal(datos, "precioReferencial"),
    )


# Lectura de campos.


def _texto(datos: dict[str, Any], campo: str) -> str:
    valor = datos.get(campo)
    if not isinstance(valor, str) or not valor.strip():
        raise MensajeInvalidoError(f"Falta {campo}.")
    return valor


def _texto_opcional(datos: dict[str, Any], campo: str) -> str | None:
    valor = datos.get(campo)
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise MensajeInvalidoError(f"{campo} no es texto.")
    return valor


def _uuid(datos: dict[str, Any], campo: str) -> str:
    valor = _texto(datos, campo)
    try:
        return str(uuid.UUID(valor))
    except ValueError as error:
        raise MensajeInvalidoError(f"{campo} no es un UUID.") from error


def _uuid_opcional(datos: dict[str, Any], campo: str) -> str | None:
    return None if datos.get(campo) is None else _uuid(datos, campo)


def _decimal(datos: dict[str, Any], campo: str) -> Decimal:
    valor = datos.get(campo)
    if isinstance(valor, bool) or not isinstance(valor, int | float | str):
        raise MensajeInvalidoError(f"Falta {campo} (número).")
    try:
        numero = Decimal(str(valor))
    except InvalidOperation as error:
        raise MensajeInvalidoError(f"{campo} no es un número.") from error
    if not numero.is_finite():
        raise MensajeInvalidoError(f"{campo} no es un número.")
    return numero


def _entero(datos: dict[str, Any], campo: str) -> int:
    valor = datos.get(campo)
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise MensajeInvalidoError(f"Falta {campo} (entero).")
    return valor


def _fecha(datos: dict[str, Any], campo: str) -> date:
    try:
        return date.fromisoformat(_texto(datos, campo))
    except ValueError as error:
        raise MensajeInvalidoError(f"{campo} no es una fecha AAAA-MM-DD.") from error


def _instante(datos: dict[str, Any], campo: str) -> datetime:
    try:
        return leer_instante(_texto(datos, campo))
    except ValueError as error:
        raise MensajeInvalidoError(f"{campo} no es una fecha ISO 8601 con zona.") from error


def _periodo(datos: dict[str, Any], campo: str) -> str:
    valor = _texto(datos, campo)
    if len(valor) != 7 or valor[4] != "-" or not (valor[:4] + valor[5:]).isdigit():
        raise MensajeInvalidoError(f"{campo} no es un periodo AAAA-MM.")
    return valor
