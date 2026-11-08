"""403 + 402: una salida atípica recalcula el riesgo de la obra con motivo CONSUMO_ATIPICO y `atipicos_90d` la cuenta.

Detección real (paso previo), recálculo real de la 402 (`ServicioRiesgo` con su modelo `rr-*` del repositorio),
PostgreSQL y MongoDB reales.
"""

from pathlib import Path
from typing import Any

import pytest
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.atipicos import ModeloFijo
from apoyo.eventos import OBRA_1, Eventos, movimiento, obra_registrada
from ia.atipicos.infraestructura.eventos import registrar_deteccion
from ia.atipicos.infraestructura.riesgo import RecalculoRiesgo402
from ia.config import Configuracion
from ia.riesgo.aplicacion.recalcular import clave_evento
from ia.riesgo.infraestructura.componer import crear_servicio_riesgo
from ia.riesgo.infraestructura.modelo_xgboost import cargar_modelo

pytestmark = pytest.mark.integracion

MODELOS = Path(__file__).resolve().parents[2] / "modelos"


async def test_un_consumo_atipico_deja_una_prediccion_con_motivo_consumo_atipico(
    motor: AsyncEngine, ia_db: AsyncDatabase[dict[str, Any]]
) -> None:
    eventos = Eventos(motor)
    modelo_riesgo = cargar_modelo(MODELOS)
    assert modelo_riesgo is not None
    riesgo = crear_servicio_riesgo(Configuracion(), motor, ia_db, eventos.reloj, modelo=modelo_riesgo)
    registrar_deteccion(
        eventos.consumidor,
        motor,
        ia_db,
        eventos.reloj,
        ModeloFijo(atipica=True),
        notificador=RecalculoRiesgo402(riesgo),
    )
    await eventos.publicar("ObraRegistrada", obra_registrada())
    await eventos.publicar(
        "ContratoRegistrado", {"obraId": OBRA_1, "contratoId": "c0000000-0000-4000-8000-000000000001"}
    )

    evento_id = await eventos.publicar("MovimientoRegistrado", movimiento())
    await eventos.publicar("MovimientoRegistrado", movimiento(tipo="INGRESO"))  # no evalúa ni recalcula

    predicciones = [d async for d in ia_db["prediccion"].find({"obra_id": OBRA_1})]
    assert [(p["motivo"], p["clave"]) for p in predicciones] == [("CONSUMO_ATIPICO", clave_evento(OBRA_1, evento_id))]
    assert predicciones[0]["variables"]["atipicos_90d"] == 1
    assert await ia_db["consumo_atipico"].count_documents({"obra_id": OBRA_1}) == 1

    # El mismo evento otra vez: ya aplicado, no se recalcula de nuevo.
    await eventos.publicar("MovimientoRegistrado", movimiento(), id_=evento_id)
    assert await ia_db["prediccion"].count_documents({"obra_id": OBRA_1}) == 1
