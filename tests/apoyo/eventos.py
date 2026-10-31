"""`datos` de los 6 eventos con los valores de la 011 (obra 1 y obra 2) y un procesador de eventos de prueba."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.mensajeria import CanalSimulado, mensaje, sobre
from ia.comun.tiempo import RelojFijo
from ia.mensajeria.consumidor import ConsumidorMensajes
from ia.reportes.infraestructura import eventos as claves
from ia.reportes.infraestructura.eventos import registrar_manejadores

# D = 2026-10-28 en La Paz (12:00), como en el ejemplo de docs/datos-prueba.md.
AHORA = datetime(2026, 10, 28, 16, 0, tzinfo=UTC)

OBRA_1 = "b0000000-0000-4000-8000-000000000001"
OBRA_2 = "b0000000-0000-4000-8000-000000000002"
RESIDENTE_1 = "a0000000-0000-4000-8000-000000000003"
MAESTRO_1 = "a0000000-0000-4000-8000-000000000005"
ALMACENERO_1 = "a0000000-0000-4000-8000-000000000006"
CLIENTE_1 = "a0000000-0000-4000-8000-000000000007"
PLANILLA_101 = "d0000000-0000-4000-8000-000000000101"
PLANILLA_102 = "d0000000-0000-4000-8000-000000000102"
PLANILLA_202 = "d0000000-0000-4000-8000-000000000202"
COBRO_101 = "c2000000-0000-4000-8000-000000000101"
COBRO_102 = "c2000000-0000-4000-8000-000000000102"
PARTIDA_102 = "b1000000-0000-4000-8000-000000000102"
CEMENTO = "e0000000-0000-4000-8000-000000000001"

CLAVE_POR_TIPO = {
    "ObraRegistrada": claves.OBRA_REGISTRADA,
    "ContratoRegistrado": claves.CONTRATO_REGISTRADO,
    "PlanillaAprobada": claves.PLANILLA_APROBADA,
    "PlanillaObservada": claves.PLANILLA_OBSERVADA,
    "CobroEmitido": claves.COBRO_EMITIDO,
    "MovimientoRegistrado": claves.MOVIMIENTO_REGISTRADO,
}


def obra_registrada(obra_id: str = OBRA_1, **cambios: Any) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "obraId": obra_id,
        "nombre": "Edificio Residencial Las Palmeras",
        "ubicacion": "Av. Beni entre 3er y 4to anillo, Santa Cruz de la Sierra",
        "zona": "NORTE",
        "fechaInicio": "2026-06-30",  # D − 120
        "asignaciones": {
            "residenteId": RESIDENTE_1,
            "maestroId": MAESTRO_1,
            "almaceneroId": ALMACENERO_1,
            "clienteId": CLIENTE_1,
        },
        "contrato": {
            "monto": 2773800.00,
            "plazoDias": 360,
            "anticipoPct": 10,
            "retencionPct": 7,
            "firmadoEn": "2026-06-20",
        },
    }
    datos.update(cambios)
    return datos


def planilla_aprobada_101() -> dict[str, Any]:
    return {
        "planillaId": PLANILLA_101,
        "obraId": OBRA_1,
        "periodo": "2026-07",
        "neto": 290134.80,
        "cobroId": COBRO_101,
        "lineas": [
            {"partidaId": "b1000000-0000-4000-8000-000000000101", "cantidadPeriodo": 1200, "monto": 55800.00},
            {"partidaId": PARTIDA_102, "cantidadPeriodo": 120, "monto": 293760.00},
        ],
    }


def planilla_aprobada_102() -> dict[str, Any]:
    return {
        "planillaId": PLANILLA_102,
        "obraId": OBRA_1,
        "periodo": "2026-08",
        "neto": 365764.40,
        "cobroId": COBRO_102,
        "lineas": [
            {"partidaId": PARTIDA_102, "cantidadPeriodo": 60, "monto": 146880.00},
            {"partidaId": "b1000000-0000-4000-8000-000000000103", "cantidadPeriodo": 50, "monto": 168250.00},
            {"partidaId": "b1000000-0000-4000-8000-000000000104", "cantidadPeriodo": 300, "monto": 125550.00},
        ],
    }


def cobro_emitido_101(vence_en: str = "2026-10-18") -> dict[str, Any]:
    return {"planillaId": PLANILLA_101, "cobroId": COBRO_101, "numero": "COB-2026-0001", "venceEn": vence_en}


def movimiento(
    movimiento_id: str | None = None, tipo: str = "SALIDA", cantidad: float = 830, **cambios: Any
) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "movimientoId": movimiento_id or str(uuid.uuid4()),
        "obraId": OBRA_1,
        "materialId": CEMENTO,
        "tipo": tipo,
        "cantidad": cantidad,
        "partidaId": PARTIDA_102 if tipo == "SALIDA" else None,
        "periodo": "2026-07",
        "superaStock": False,
        "registradoEn": "2026-07-19T14:00:00Z",
        "precioReferencial": 62.00,
    }
    datos.update(cambios)
    return datos


class Eventos:
    """Consumidor real con los manejadores de `reportes`, sobre la base de prueba y un canal simulado."""

    def __init__(self, motor: AsyncEngine, ahora: datetime = AHORA) -> None:
        self.motor = motor
        self.reloj = RelojFijo(ahora)
        self.consumidor = ConsumidorMensajes()
        registrar_manejadores(self.consumidor, motor, self.reloj)
        self.canal = CanalSimulado()

    async def publicar(
        self, tipo: str, datos: dict[str, Any], *, id_: str | None = None, ocurrido_en: str = "2026-10-28T14:30:00Z"
    ) -> str:
        contenido = sobre(tipo, datos, id_=id_, ocurrido_en=ocurrido_en)
        await self.consumidor.procesar(mensaje(contenido, CLAVE_POR_TIPO[tipo]), self.canal)
        return str(contenido["id"])


async def filas(motor: AsyncEngine, tabla: Any, **filtro: Any) -> list[RowMapping]:
    consulta = select(tabla)
    for columna, valor in filtro.items():
        consulta = consulta.where(tabla.c[columna] == valor)
    async with motor.connect() as conexion:
        return list((await conexion.execute(consulta)).mappings())


async def fila(motor: AsyncEngine, tabla: Any, **filtro: Any) -> RowMapping:
    encontradas = await filas(motor, tabla, **filtro)
    assert len(encontradas) == 1, f"Se esperaba 1 fila en {tabla.name} con {filtro}, hay {len(encontradas)}"
    return encontradas[0]
