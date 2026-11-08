"""Detección en el worker al aplicar `MovimientoRegistrado` (403, diseño «Detección en el servicio» y «Pruebas»).

Consumidor real con los manejadores de la 401, PostgreSQL y MongoDB reales y un canal simulado.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from bson.decimal128 import Decimal128
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.atipicos import ModeloFijo, modelo_sintetico_chico
from apoyo.eventos import AHORA, CEMENTO, OBRA_1, OBRA_2, PARTIDA_102, Eventos, fila, movimiento
from ia.atipicos.aplicacion.deteccion import DetectarConsumoAtipico
from ia.atipicos.aplicacion.puertos import ModeloAtipicos
from ia.atipicos.dominio.historial import HistorialEnMemoria
from ia.atipicos.dominio.variables import SalidaMaterial, calcular_variables_salida
from ia.atipicos.infraestructura.eventos import registrar_deteccion
from ia.atipicos.infraestructura.repositorio_mongo import COLECCION
from ia.atipicos.infraestructura.salidas_sql import HistorialSalidasSql
from ia.mensajeria.consumidor import COLA_FALLIDOS
from ia.reportes.infraestructura.eventos import leer_movimiento
from ia.reportes.infraestructura.repositorio_sql import BaseReportesSql
from ia.reportes.infraestructura.tablas import rpt_movimiento

pytestmark = pytest.mark.integracion

Base = AsyncDatabase[dict[str, Any]]


def con_deteccion(
    motor: AsyncEngine, ia_db: Base, modelo: ModeloAtipicos | None
) -> tuple[Eventos, DetectarConsumoAtipico]:
    eventos = Eventos(motor)
    deteccion = registrar_deteccion(eventos.consumidor, motor, ia_db, eventos.reloj, modelo)
    return eventos, deteccion


async def documentos(ia_db: Base) -> list[dict[str, Any]]:
    return [d async for d in ia_db[COLECCION].find({})]


async def test_salida_atipica_crea_un_consumo_pendiente_y_la_proyeccion_sigue(motor: AsyncEngine, ia_db: Base) -> None:
    modelo = ModeloFijo(atipica=True)
    eventos, _ = con_deteccion(motor, ia_db, modelo)
    movimiento_id = str(uuid.uuid4())
    await eventos.publicar("MovimientoRegistrado", movimiento(movimiento_id, cantidad=120))

    [documento] = await documentos(ia_db)
    assert documento == {
        "_id": movimiento_id,
        "obra_id": OBRA_1,
        "material_id": CEMENTO,
        "partida_id": PARTIDA_102,
        "cantidad": Decimal128("120"),
        "periodo": "2026-07",
        "registrado_en": datetime(2026, 7, 19, 14, 0, tzinfo=UTC),
        "puntaje": 0.71,
        "motivos": ["Se registró fuera del horario habitual (22:40)"],
        "variables": modelo.recibidas[0].como_dict(),
        "version_modelo": "ca-0.0.1",
        "detectado_en": AHORA,
        "estado_revision": "PENDIENTE",
        "revisado_por": None,
        "revisado_en": None,
        "comentario": None,
    }
    assert (await fila(motor, rpt_movimiento, movimiento_id=movimiento_id))["cantidad"] == Decimal("120.000")
    assert eventos.canal.publicados == []


async def test_salida_normal_no_crea_nada(motor: AsyncEngine, ia_db: Base) -> None:
    modelo = ModeloFijo(atipica=False)
    eventos, _ = con_deteccion(motor, ia_db, modelo)
    await eventos.publicar("MovimientoRegistrado", movimiento())
    assert len(modelo.recibidas) == 1
    assert await documentos(ia_db) == []


async def test_un_ingreso_no_se_evalua(motor: AsyncEngine, ia_db: Base) -> None:
    modelo = ModeloFijo(atipica=True)
    eventos, _ = con_deteccion(motor, ia_db, modelo)
    await eventos.publicar("MovimientoRegistrado", movimiento(tipo="INGRESO"))
    assert modelo.recibidas == []
    assert await documentos(ia_db) == []


async def test_el_mismo_evento_dos_veces_se_evalua_y_guarda_una_sola_vez(motor: AsyncEngine, ia_db: Base) -> None:
    modelo = ModeloFijo(atipica=True)
    eventos, _ = con_deteccion(motor, ia_db, modelo)
    evento_id = str(uuid.uuid4())
    datos = movimiento()
    await eventos.publicar("MovimientoRegistrado", datos, id_=evento_id)
    await eventos.publicar("MovimientoRegistrado", datos, id_=evento_id)
    assert len(modelo.recibidas) == 1  # el repetido está en evento_aplicado: se confirma sin evaluar
    assert len(await documentos(ia_db)) == 1
    assert len(eventos.canal.confirmados) == 2


async def test_un_reintento_repite_la_evaluacion_sin_duplicar(motor: AsyncEngine, ia_db: Base) -> None:
    # Como si la transacción de la 401 hubiera fallado después del paso previo: el evento no quedó aplicado.
    _, deteccion = con_deteccion(motor, ia_db, ModeloFijo(atipica=True, puntaje=0.8))
    datos = movimiento()
    primero = await deteccion.evaluar(str(uuid.uuid4()), leer_movimiento(datos))
    segundo = await deteccion.evaluar(str(uuid.uuid4()), leer_movimiento(datos))
    assert (primero.guardada, segundo.guardada) == (True, False)
    [documento] = await documentos(ia_db)
    assert documento["puntaje"] == 0.8


async def test_la_salida_que_ya_trajo_la_carga_inicial_no_se_compara_consigo_misma(
    motor: AsyncEngine, ia_db: Base
) -> None:
    movimiento_id = str(uuid.uuid4())
    datos = movimiento(movimiento_id, registradoEn="2026-07-19T14:00:00Z")
    anterior = movimiento(registradoEn="2026-07-19T13:00:00Z")
    async with BaseReportesSql(motor).transaccion() as repo:
        await repo.insertar_movimiento(leer_movimiento(anterior))
        await repo.insertar_movimiento(leer_movimiento(datos))  # la trajo la carga inicial

    modelo = ModeloFijo(atipica=False)
    eventos, _ = con_deteccion(motor, ia_db, modelo)
    await eventos.publicar("MovimientoRegistrado", datos)
    [variables] = modelo.recibidas
    assert variables.salidas_dia == 2  # la anterior y ella, no ella dos veces
    assert variables.minutos_anterior == pytest.approx(60)


async def test_sin_modelo_la_proyeccion_sigue_sin_detectar(
    motor: AsyncEngine, ia_db: Base, caplog: pytest.LogCaptureFixture
) -> None:
    eventos, _ = con_deteccion(motor, ia_db, None)
    movimiento_id = str(uuid.uuid4())
    await eventos.publicar("MovimientoRegistrado", movimiento(movimiento_id))
    assert await fila(motor, rpt_movimiento, movimiento_id=movimiento_id)
    assert await documentos(ia_db) == []
    assert eventos.canal.publicados == []
    assert "no hay modelo de consumos atípicos" in caplog.text


async def test_datos_invalidos_van_a_fallidos_sin_evaluar(motor: AsyncEngine, ia_db: Base) -> None:
    modelo = ModeloFijo(atipica=True)
    eventos, _ = con_deteccion(motor, ia_db, modelo)
    await eventos.publicar("MovimientoRegistrado", movimiento(partidaId=None))
    assert [p.cola for p in eventos.canal.publicados] == [COLA_FALLIDOS]
    assert modelo.recibidas == []


async def test_con_el_modelo_real_una_salida_enorme_de_noche_es_atipica(motor: AsyncEngine, ia_db: Base) -> None:
    eventos, _ = con_deteccion(motor, ia_db, modelo_sintetico_chico())
    movimiento_id = str(uuid.uuid4())
    # 02:40 en La Paz, sin stock suficiente y 1000 veces la cantidad habitual.
    await eventos.publicar(
        "MovimientoRegistrado",
        movimiento(movimiento_id, cantidad=90_000, superaStock=True, registradoEn="2026-07-19T06:40:00Z"),
    )
    [documento] = await documentos(ia_db)
    assert documento["_id"] == movimiento_id
    assert 0 < documento["puntaje"] <= 1
    assert 1 <= len(documento["motivos"]) <= 3
    assert "Se registró fuera del horario habitual (02:40)" in documento["motivos"]
    assert documento["version_modelo"] == "ca-0.9.0"


async def test_el_historial_de_la_base_es_el_mismo_que_el_de_memoria(motor: AsyncEngine) -> None:
    inicio = datetime(2026, 7, 1, 13, 0, tzinfo=UTC)
    datos = []
    for n in range(70):
        obra = OBRA_1 if n % 3 else OBRA_2
        material = CEMENTO if n % 4 else "e0000000-0000-4000-8000-000000000002"
        momento = inicio + timedelta(hours=7 * n)
        datos.append(
            movimiento(
                str(uuid.UUID(int=n + 1)),
                cantidad=round(10 + (n * 7.3) % 25, 3),
                obraId=obra,
                materialId=material,
                periodo=momento.strftime("%Y-%m"),
                registradoEn=momento.isoformat().replace("+00:00", "Z"),
            )
        )
    datos.append(movimiento(tipo="INGRESO", cantidad=5000, registradoEn="2026-07-02T13:00:00Z"))
    async with BaseReportesSql(motor).transaccion() as repo:
        for d in datos:
            await repo.insertar_movimiento(leer_movimiento(d))

    salidas = [
        SalidaMaterial(
            movimiento_id=d["movimientoId"],
            obra_id=d["obraId"],
            material_id=d["materialId"],
            cantidad=float(d["cantidad"]),
            periodo=d["periodo"],
            supera_stock=d["superaStock"],
            registrado_en=datetime.fromisoformat(d["registradoEn"]),
        )
        for d in datos
        if d["tipo"] == "SALIDA"
    ]
    fuente = HistorialSalidasSql(motor)
    memoria = HistorialEnMemoria()
    for salida in salidas:
        desde_base = calcular_variables_salida(salida, await fuente.historial_de(salida))
        en_memoria = calcular_variables_salida(salida, memoria.historial_de(salida))
        # La base suma en decimal y la memoria en float: iguales salvo el último bit.
        assert desde_base.vector() == pytest.approx(en_memoria.vector(), rel=1e-9), salida.movimiento_id
        memoria.agregar(salida)


# Paso 5: recálculo del riesgo de retraso (402) después de confirmar la transacción.


class NotificadorDePrueba:
    def __init__(self, falla: bool = False) -> None:
        self.llamadas: list[tuple[str, str]] = []
        self.falla = falla

    async def consumo_atipico(self, obra_id: str, evento_id: str) -> None:
        self.llamadas.append((obra_id, evento_id))
        if self.falla:
            raise RuntimeError("la 402 no responde")


def con_recalculo(motor: AsyncEngine, ia_db: Base, modelo: ModeloAtipicos, notificador: NotificadorDePrueba) -> Eventos:
    eventos = Eventos(motor)
    registrar_deteccion(eventos.consumidor, motor, ia_db, eventos.reloj, modelo, notificador=notificador)
    return eventos


async def test_un_atipico_recalcula_el_riesgo_de_la_obra_una_vez(motor: AsyncEngine, ia_db: Base) -> None:
    notificador = NotificadorDePrueba()
    eventos = con_recalculo(motor, ia_db, ModeloFijo(atipica=True), notificador)
    evento_id = str(uuid.uuid4())
    datos = movimiento()
    await eventos.publicar("MovimientoRegistrado", datos, id_=evento_id)
    assert notificador.llamadas == [(OBRA_1, evento_id)]
    await eventos.publicar("MovimientoRegistrado", datos, id_=evento_id)  # repetido: no se aplica ni recalcula
    assert notificador.llamadas == [(OBRA_1, evento_id)]


async def test_una_salida_normal_o_un_ingreso_no_recalculan(motor: AsyncEngine, ia_db: Base) -> None:
    notificador = NotificadorDePrueba()
    eventos = con_recalculo(motor, ia_db, ModeloFijo(atipica=False), notificador)
    await eventos.publicar("MovimientoRegistrado", movimiento())
    await eventos.publicar("MovimientoRegistrado", movimiento(tipo="INGRESO"))
    assert notificador.llamadas == []


async def test_si_el_recalculo_falla_el_evento_queda_aplicado_sin_reintento(
    motor: AsyncEngine, ia_db: Base, caplog: pytest.LogCaptureFixture
) -> None:
    notificador = NotificadorDePrueba(falla=True)
    eventos = con_recalculo(motor, ia_db, ModeloFijo(atipica=True), notificador)
    movimiento_id = str(uuid.uuid4())
    evento_id = await eventos.publicar("MovimientoRegistrado", movimiento(movimiento_id))
    assert notificador.llamadas == [(OBRA_1, evento_id)]
    assert eventos.canal.publicados == []
    assert len(eventos.canal.confirmados) == 1
    assert await fila(motor, rpt_movimiento, movimiento_id=movimiento_id)
    assert "No se pudo recalcular el riesgo" in caplog.text


async def test_sin_la_402_no_hay_paso_de_recalculo(motor: AsyncEngine, ia_db: Base) -> None:
    eventos, _ = con_deteccion(motor, ia_db, ModeloFijo(atipica=True))
    registro_movimiento = eventos.consumidor._por_tipo["MovimientoRegistrado"]
    assert len(registro_movimiento.previos) == 1
    assert registro_movimiento.posteriores == []
    await eventos.publicar("MovimientoRegistrado", movimiento())
    assert len(await documentos(ia_db)) == 1
