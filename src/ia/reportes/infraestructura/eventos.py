"""Adaptador de entrada de eventos: traduce `datos` de los 6 eventos a lecturas y registra sus manejadores.

Lector tolerante: los campos desconocidos se ignoran; `ubicacion` y `zona` (`ObraRegistrada`) y `precioReferencial`
(`MovimientoRegistrado`) pueden faltar. Un campo obligatorio que falta o no tiene el formato → `MensajeInvalidoError`
(directo a `plataforma.fallidos`).
"""

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ia.comun.tiempo import Reloj, fecha_en_zona
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
from ia.reportes.infraestructura import campos
from ia.reportes.infraestructura.campos import DatoInvalido
from ia.reportes.infraestructura.repositorio_sql import RepositorioReportesSql

OBRA_REGISTRADA = "evento.obras.ObraRegistrada"
CONTRATO_REGISTRADO = "evento.finanzas.ContratoRegistrado"
PLANILLA_APROBADA = "evento.obras.PlanillaAprobada"
PLANILLA_OBSERVADA = "evento.obras.PlanillaObservada"
COBRO_EMITIDO = "evento.finanzas.CobroEmitido"
MOVIMIENTO_REGISTRADO = "evento.materiales.MovimientoRegistrado"

Aplicar = Callable[[Sobre, RepositorioReportesSql], Awaitable[object]]


def registrar_manejadores(
    consumidor: ConsumidorMensajes, motor: AsyncEngine, reloj: Reloj, zona_horaria: str = "America/La_Paz"
) -> Proyecciones:
    """Un manejador por evento: inbox + proyección en la misma transacción."""
    proyecciones = Proyecciones(reloj)

    def en_transaccion(aplicar: Aplicar) -> Callable[[Sobre, AsyncConnection], Awaitable[None]]:
        async def aplicar_con_conexion(sobre: Sobre, conexion: AsyncConnection) -> None:
            try:
                await aplicar(sobre, RepositorioReportesSql(conexion))
            except DatoInvalido as error:
                raise MensajeInvalidoError(str(error)) from error

        return aplicar_con_conexion

    manejadores: dict[str, Aplicar] = {
        OBRA_REGISTRADA: lambda s, repo: proyecciones.obra_registrada(repo, leer_obra_registrada(s.datos)),
        CONTRATO_REGISTRADO: lambda s, repo: proyecciones.contrato_registrado(repo, campos.uuid_(s.datos, "obraId")),
        PLANILLA_APROBADA: lambda s, repo: proyecciones.planilla_aprobada(repo, leer_planilla_aprobada(s)),
        PLANILLA_OBSERVADA: lambda s, repo: proyecciones.planilla_observada(repo, leer_planilla_observada(s)),
        COBRO_EMITIDO: lambda s, repo: proyecciones.cobro_emitido(repo, leer_cobro_emitido(s, zona_horaria)),
        MOVIMIENTO_REGISTRADO: lambda s, repo: proyecciones.movimiento_registrado(repo, leer_movimiento(s.datos)),
    }
    for clave, aplicar in manejadores.items():
        consumidor.registrar(clave, manejador_con_inbox(motor, en_transaccion(aplicar), reloj))
    return proyecciones


# Traductores (lanzan DatoInvalido).


def leer_obra_registrada(datos: dict[str, Any]) -> ObraLeida:
    asignaciones = campos.objeto(datos, "asignaciones")
    contrato = campos.objeto(datos, "contrato")
    return ObraLeida(
        obra_id=campos.uuid_(datos, "obraId"),
        nombre=campos.texto(datos, "nombre"),
        ubicacion=campos.texto_opcional(datos, "ubicacion"),
        zona=campos.texto_opcional(datos, "zona"),
        cliente_id=campos.uuid_opcional(asignaciones, "clienteId"),
        residente_id=campos.uuid_opcional(asignaciones, "residenteId"),
        maestro_id=campos.uuid_opcional(asignaciones, "maestroId"),
        almacenero_id=campos.uuid_opcional(asignaciones, "almaceneroId"),
        fecha_inicio=campos.fecha(datos, "fechaInicio"),
        plazo_dias=campos.entero(contrato, "plazoDias"),
        monto_contrato=campos.decimal(contrato, "monto"),
    )


def leer_planilla_aprobada(sobre: Sobre) -> PlanillaAprobadaLeida:
    datos = sobre.datos
    return PlanillaAprobadaLeida(
        planilla_id=campos.uuid_(datos, "planillaId"),
        obra_id=campos.uuid_(datos, "obraId"),
        periodo=campos.periodo(datos, "periodo"),
        neto=campos.decimal(datos, "neto"),
        cobro_id=campos.uuid_opcional(datos, "cobroId"),
        montos_lineas=tuple(campos.decimal(linea, "monto") for linea in campos.lista_de_objetos(datos, "lineas")),
        aprobada_en=sobre.ocurrido_en,
    )


def leer_planilla_observada(sobre: Sobre) -> PlanillaObservadaLeida:
    datos = sobre.datos
    return PlanillaObservadaLeida(
        planilla_id=campos.uuid_(datos, "planillaId"),
        obra_id=campos.uuid_(datos, "obraId"),
        motivo=campos.texto_opcional(datos, "motivo"),
        origen=campos.texto_opcional(datos, "origen"),
        ocurrida_en=sobre.ocurrido_en,
    )


def leer_cobro_emitido(sobre: Sobre, zona_horaria: str) -> CobroEmitidoLeido:
    datos = sobre.datos
    return CobroEmitidoLeido(
        cobro_id=campos.uuid_(datos, "cobroId"),
        planilla_id=campos.uuid_(datos, "planillaId"),
        numero=campos.texto(datos, "numero"),
        vence_en=campos.fecha(datos, "venceEn"),
        emitido_en=fecha_en_zona(sobre.ocurrido_en, zona_horaria) if sobre.ocurrido_en else None,
    )


def leer_movimiento(datos: dict[str, Any], campo_id: str = "movimientoId") -> MovimientoLeido:
    """`MovimientoRegistrado` (`movimientoId`) o un elemento de `GET /obras/{id}/movimientos` (`id`, con el
    `obraId` agregado por quien llama)."""
    tipo = campos.texto(datos, "tipo")
    if tipo not in TIPOS_MOVIMIENTO:
        raise DatoInvalido(f"Tipo de movimiento desconocido: {tipo}.")
    partida_id = campos.uuid_opcional(datos, "partidaId")
    if tipo == SALIDA and partida_id is None:
        raise DatoInvalido("Una SALIDA necesita partidaId.")
    return MovimientoLeido(
        movimiento_id=campos.uuid_(datos, campo_id),
        obra_id=campos.uuid_(datos, "obraId"),
        material_id=campos.texto(datos, "materialId"),
        tipo=tipo,
        cantidad=campos.decimal(datos, "cantidad"),
        partida_id=partida_id,
        periodo=campos.periodo(datos, "periodo"),
        supera_stock=campos.booleano(datos, "superaStock"),
        registrado_en=campos.instante(datos, "registradoEn"),
        precio_referencial=campos.decimal_opcional(datos, "precioReferencial"),
    )
