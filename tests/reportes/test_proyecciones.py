"""Proyecciones de los 6 eventos con PostgreSQL real (consumidor + inbox + caso de uso + SQL)."""

import asyncio
import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.eventos import (
    CEMENTO,
    CLIENTE_1,
    COBRO_101,
    OBRA_1,
    OBRA_2,
    PARTIDA_102,
    PLANILLA_101,
    PLANILLA_202,
    RESIDENTE_1,
    Eventos,
    cobro_emitido_101,
    fila,
    filas,
    movimiento,
    obra_registrada,
    planilla_aprobada_101,
    planilla_aprobada_102,
)
from ia.mensajeria.inbox import evento_aplicado
from ia.reportes.infraestructura.tablas import (
    rpt_cobro,
    rpt_consumo_material,
    rpt_movimiento,
    rpt_obra_resumen,
    rpt_planilla,
)

pytestmark = pytest.mark.integracion


async def test_obra_registrada_crea_la_obra_sin_contrato(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    obra = await fila(motor, rpt_obra_resumen, obra_id=OBRA_1)
    assert obra["nombre"] == "Edificio Residencial Las Palmeras"
    assert obra["ubicacion"].startswith("Av. Beni")
    assert obra["zona"] == "NORTE"
    assert str(obra["cliente_id"]) == CLIENTE_1
    assert str(obra["residente_id"]) == RESIDENTE_1
    assert obra["estado"] == "SIN_CONTRATO"
    assert obra["fecha_inicio"] == date(2026, 6, 30)
    assert obra["plazo_dias"] == 360
    assert obra["monto_contrato"] == Decimal("2773800.00")
    assert obra["avance_pct"] == Decimal("0.00")
    assert obra["plazo_pct"] == Decimal("33.33")
    assert eventos.canal.publicados == []


async def test_obra_registrada_sin_ubicacion_ni_zona_y_con_campos_desconocidos(motor: AsyncEngine) -> None:
    datos = obra_registrada(campoNuevo="se ignora")
    del datos["ubicacion"]
    del datos["zona"]
    await Eventos(motor).publicar("ObraRegistrada", datos)
    obra = await fila(motor, rpt_obra_resumen, obra_id=OBRA_1)
    assert obra["ubicacion"] is None
    assert obra["zona"] is None


async def test_contrato_registrado_pone_en_ejecucion_y_otra_obra_registrada_no_lo_revierte(
    motor: AsyncEngine,
) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    await eventos.publicar(
        "ContratoRegistrado", {"obraId": OBRA_1, "contratoId": "c0000000-0000-4000-8000-000000000001"}
    )
    await eventos.publicar("ObraRegistrada", obra_registrada(nombre="Las Palmeras (corregido)"))
    obra = await fila(motor, rpt_obra_resumen, obra_id=OBRA_1)
    assert obra["estado"] == "EN_EJECUCION"
    assert obra["nombre"] == "Las Palmeras (corregido)"


async def test_obra_registrada_vieja_sin_zona_no_borra_la_zona(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    vieja = obra_registrada()
    del vieja["zona"]
    await eventos.publicar("ObraRegistrada", vieja)
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA_1))["zona"] == "NORTE"


async def test_contrato_sin_obra_falla_se_reintenta_y_al_tercer_fallo_va_a_fallidos(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar(
        "ContratoRegistrado", {"obraId": OBRA_2, "contratoId": "c0000000-0000-4000-8000-000000000002"}
    )
    primero = eventos.canal.publicados[-1]
    assert primero.cola == "ia"
    assert primero.encabezados["x-intentos"] == 1
    assert "ObraNoRegistrada" in primero.encabezados["x-error"]
    assert await filas(motor, evento_aplicado) == []

    await eventos.consumidor.procesar(eventos.canal.reentregar(primero), eventos.canal)
    await eventos.consumidor.procesar(eventos.canal.reentregar(eventos.canal.publicados[-1]), eventos.canal)
    ultimo = eventos.canal.publicados[-1]
    assert ultimo.cola == "plataforma.fallidos"
    assert ultimo.encabezados["x-intentos"] == 3
    assert ultimo.encabezados["x-clave-origen"] == "evento.finanzas.ContratoRegistrado"


async def test_contrato_reintentado_despues_de_la_obra_se_aplica(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar(
        "ContratoRegistrado", {"obraId": OBRA_1, "contratoId": "c0000000-0000-4000-8000-000000000001"}
    )
    await eventos.publicar("ObraRegistrada", obra_registrada())
    await eventos.consumidor.procesar(eventos.canal.reentregar(eventos.canal.publicados[-1]), eventos.canal)
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA_1))["estado"] == "EN_EJECUCION"


async def test_planillas_aprobadas_guardan_la_planilla_el_avance_y_el_cobro(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    await eventos.publicar("PlanillaAprobada", planilla_aprobada_101(), ocurrido_en="2026-09-18T14:00:00Z")
    await eventos.publicar("PlanillaAprobada", planilla_aprobada_102())

    planilla = await fila(motor, rpt_planilla, planilla_id=PLANILLA_101)
    assert planilla["estado"] == "APROBADA"
    assert planilla["periodo"] == "2026-07"
    assert planilla["neto"] == Decimal("290134.80")
    assert planilla["monto_periodo"] == Decimal("349560.00")
    assert planilla["avance_periodo_pct"] == Decimal("12.60")
    assert planilla["aprobada_en"].isoformat() == "2026-09-18T14:00:00+00:00"
    assert planilla["cargada_en"] is None
    # Misma regla que Obras: (349.560,00 + 440.680,00) / 2.773.800,00 = 28,49 %.
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA_1))["avance_pct"] == Decimal("28.49")

    cobro = await fila(motor, rpt_cobro, cobro_id=COBRO_101)
    assert str(cobro["obra_id"]) == OBRA_1
    assert str(cobro["planilla_id"]) == PLANILLA_101
    assert cobro["monto"] == Decimal("290134.80")
    assert str(cobro["cliente_id"]) == CLIENTE_1
    assert cobro["zona"] == "NORTE"
    assert cobro["numero"] is None
    assert cobro["estado"] is None


async def test_planilla_aprobada_sin_obra_se_reintenta(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("PlanillaAprobada", planilla_aprobada_101())
    assert eventos.canal.publicados[-1].cola == "ia"
    assert await filas(motor, rpt_planilla) == []


async def _cobro_final(motor: AsyncEngine, cobro_primero: bool) -> dict[str, object]:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    pasos = [
        ("CobroEmitido", cobro_emitido_101(vence_en="2026-11-20"), "2026-10-21T15:00:00Z"),
        ("PlanillaAprobada", planilla_aprobada_101(), "2026-10-21T15:00:05Z"),
    ]
    for tipo, datos, ocurrido in pasos if cobro_primero else reversed(pasos):
        await eventos.publicar(tipo, datos, ocurrido_en=ocurrido)
    cobro = dict(await fila(motor, rpt_cobro, cobro_id=COBRO_101))
    del cobro["actualizado_en"]
    return cobro


async def test_cobro_emitido_antes_o_despues_de_planilla_aprobada_deja_la_misma_fila(motor: AsyncEngine) -> None:
    antes = await _cobro_final(motor, cobro_primero=True)
    async with motor.begin() as conexion:
        from sqlalchemy import text

        await conexion.execute(text("TRUNCATE rpt_obra_resumen, rpt_planilla, rpt_cobro, evento_aplicado"))
    despues = await _cobro_final(motor, cobro_primero=False)
    assert antes == despues
    assert antes["numero"] == "COB-2026-0001"
    assert antes["emitido_en"] == date(2026, 10, 21)
    assert antes["vence_en"] == date(2026, 11, 20)
    assert antes["estado"] == "PENDIENTE"
    assert antes["monto"] == Decimal("290134.80")
    assert antes["zona"] == "NORTE"


async def test_cobro_emitido_vencido_y_emision_en_hora_de_la_paz(motor: AsyncEngine) -> None:
    # 2026-09-19 02:00 UTC = 2026-09-18 22:00 en La Paz; vence 2026-10-18 < D (2026-10-28).
    await Eventos(motor).publicar("CobroEmitido", cobro_emitido_101(), ocurrido_en="2026-09-19T02:00:00Z")
    cobro = await fila(motor, rpt_cobro, cobro_id=COBRO_101)
    assert cobro["emitido_en"] == date(2026, 9, 18)
    assert cobro["estado"] == "VENCIDO"
    assert cobro["obra_id"] is None  # llega con PlanillaAprobada


async def test_planilla_observada_cuenta_y_guarda_el_ultimo_motivo(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    datos = {"planillaId": PLANILLA_202, "obraId": OBRA_2, "motivo": "Primera observación.", "origen": "CLIENTE"}
    await eventos.publicar("PlanillaObservada", datos)
    await eventos.publicar("PlanillaObservada", {**datos, "motivo": "Saldo insuficiente.", "origen": "SAGA"})
    planilla = await fila(motor, rpt_planilla, planilla_id=PLANILLA_202)
    assert planilla["estado"] == "OBSERVADA"
    assert planilla["veces_observada"] == 2
    assert planilla["ultimo_motivo"] == "Saldo insuficiente."
    assert planilla["ultimo_origen"] == "SAGA"
    assert planilla["periodo"] is None


async def test_observada_despues_aprobada_conserva_el_conteo_y_no_vuelve_a_observada(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    observacion = {"planillaId": PLANILLA_101, "obraId": OBRA_1, "motivo": "Corrijan.", "origen": "CLIENTE"}
    await eventos.publicar("PlanillaObservada", observacion)
    await eventos.publicar("PlanillaAprobada", planilla_aprobada_101())
    await eventos.publicar("PlanillaObservada", {**observacion, "motivo": "Tarde."})
    planilla = await fila(motor, rpt_planilla, planilla_id=PLANILLA_101)
    assert planilla["estado"] == "APROBADA"
    assert planilla["veces_observada"] == 2
    assert planilla["monto_periodo"] == Decimal("349560.00")


async def test_salida_suma_consumo_e_ingreso_no(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("MovimientoRegistrado", movimiento(tipo="INGRESO", cantidad=900))
    await eventos.publicar("MovimientoRegistrado", movimiento(cantidad=830))
    await eventos.publicar("MovimientoRegistrado", movimiento(cantidad=12.5))
    assert len(await filas(motor, rpt_movimiento)) == 3
    consumo = await fila(motor, rpt_consumo_material, obra_id=OBRA_1, material_id=CEMENTO)
    assert str(consumo["partida_id"]) == PARTIDA_102
    assert consumo["periodo"] == "2026-07"
    assert consumo["cantidad"] == Decimal("842.500")
    assert consumo["monto"] == Decimal("52235.00")  # 842,5 × 62,00
    assert consumo["salidas"] == 2


async def test_movimiento_repetido_no_suma_dos_veces(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    datos = movimiento(movimiento_id="f0000000-0000-4000-8000-000000010006")
    id_evento = await eventos.publicar("MovimientoRegistrado", datos)
    await eventos.publicar("MovimientoRegistrado", datos, id_=id_evento)  # mismo evento (inbox)
    await eventos.publicar("MovimientoRegistrado", datos)  # otro evento, mismo movimiento (fila)
    consumo = await fila(motor, rpt_consumo_material, obra_id=OBRA_1)
    assert consumo["cantidad"] == Decimal("830.000")
    assert consumo["salidas"] == 1
    assert len(await filas(motor, evento_aplicado)) == 2


async def test_movimiento_sin_precio_suma_monto_cero(motor: AsyncEngine) -> None:
    datos = movimiento()
    del datos["precioReferencial"]
    await Eventos(motor).publicar("MovimientoRegistrado", datos)
    assert (await fila(motor, rpt_movimiento))["precio_referencial"] is None
    consumo = await fila(motor, rpt_consumo_material, obra_id=OBRA_1)
    assert consumo["monto"] == Decimal("0.00")
    assert consumo["cantidad"] == Decimal("830.000")


async def test_mismo_evento_dos_veces_se_aplica_una_vez(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    datos = {"planillaId": PLANILLA_101, "obraId": OBRA_1, "motivo": "Corrijan.", "origen": "CLIENTE"}
    id_evento = await eventos.publicar("PlanillaObservada", datos)
    await eventos.publicar("PlanillaObservada", datos, id_=id_evento)
    assert (await fila(motor, rpt_planilla, planilla_id=PLANILLA_101))["veces_observada"] == 1
    assert len(eventos.canal.confirmados) == 3


@pytest.mark.parametrize(
    ("tipo", "datos"),
    [
        ("ObraRegistrada", {"obraId": OBRA_1}),
        ("PlanillaAprobada", {**planilla_aprobada_101(), "obraId": "no-es-uuid"}),
        ("MovimientoRegistrado", movimiento(partidaId=None)),
        ("MovimientoRegistrado", movimiento(tipo="AJUSTE")),
        ("CobroEmitido", {**cobro_emitido_101(), "venceEn": "mañana"}),
    ],
    ids=["obra-incompleta", "uuid-invalido", "salida-sin-partida", "tipo-desconocido", "fecha-invalida"],
)
async def test_datos_incompletos_van_directo_a_fallidos(
    motor: AsyncEngine, tipo: str, datos: dict[str, object]
) -> None:
    eventos = Eventos(motor)
    await eventos.publicar(tipo, datos)
    assert [p.cola for p in eventos.canal.publicados] == ["plataforma.fallidos"]
    assert eventos.canal.publicados[0].encabezados["x-intentos"] == 0
    assert json.loads(eventos.canal.publicados[0].cuerpo)["datos"] == datos
    assert await filas(motor, evento_aplicado) == []


async def test_aprobadas_en_paralelo_dejan_el_avance_correcto(motor: AsyncEngine) -> None:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada())
    await asyncio.gather(
        eventos.publicar("PlanillaAprobada", planilla_aprobada_101()),
        eventos.publicar("PlanillaAprobada", planilla_aprobada_102()),
    )
    assert (await fila(motor, rpt_obra_resumen, obra_id=OBRA_1))["avance_pct"] == Decimal("28.49")
