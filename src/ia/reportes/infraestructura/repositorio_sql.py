"""Repositorio de `reportes_db` con SQLAlchemy Core sobre una conexión con la transacción abierta."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Label, case, func, literal_column, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ia.mensajeria.inbox import evento_aplicado
from ia.reportes.aplicacion.lecturas import MovimientoLeido, ObraLeida, PlanillaAprobadaLeida
from ia.reportes.aplicacion.puertos import (
    CobroParcial,
    CobroPendiente,
    CorridaCarga,
    FilaPlanilla,
    ObraResumen,
    PlanillaGuardada,
    PlazoObra,
)
from ia.reportes.dominio.valores import (
    APROBADA,
    CARGA_EN_CURSO,
    CARGA_FALLIDA,
    EN_EJECUCION,
    OBSERVADA,
    PENDIENTE,
    SIN_CONTRATO,
    VENCIDO,
)
from ia.reportes.infraestructura.tablas import (
    carga_inicial,
    rpt_cobro,
    rpt_consumo_material,
    rpt_movimiento,
    rpt_obra_resumen,
    rpt_planilla,
)

# Clave fija del bloqueo de una sola carga inicial a la vez (pg_try_advisory_lock).
CLAVE_BLOQUEO_CARGA = 401_000_001

# `xmax = 0` en la fila devuelta por un upsert: la fila es nueva (se insertó, no se actualizó).
_INSERTADA: Label[bool] = literal_column("(xmax = 0)", Boolean).label("insertada")

_DESCRIPTIVOS_OBRA = (
    "nombre",
    "ubicacion",
    "zona",
    "cliente_id",
    "residente_id",
    "maestro_id",
    "almacenero_id",
    "fecha_inicio",
    "plazo_dias",
    "monto_contrato",
)


class RepositorioReportesSql:
    def __init__(self, conexion: AsyncConnection) -> None:
        self._c = conexion

    # rpt_obra_resumen

    async def guardar_obra(self, obra: ObraLeida, plazo_pct: Decimal, ahora: datetime) -> bool:
        valores: dict[str, Any] = {campo: getattr(obra, campo) for campo in _DESCRIPTIVOS_OBRA}
        consulta = insert(rpt_obra_resumen).values(
            obra_id=obra.obra_id,
            estado=SIN_CONTRATO,
            avance_pct=Decimal("0.00"),
            plazo_pct=plazo_pct,
            actualizado_en=ahora,
            **valores,
        )
        # Un evento viejo sin `ubicacion` o `zona` no borra lo que ya trajo la carga: se completa con COALESCE.
        actualizar = {
            campo: func.coalesce(consulta.excluded[campo], rpt_obra_resumen.c[campo]) for campo in _DESCRIPTIVOS_OBRA
        }
        upsert = consulta.on_conflict_do_update(
            index_elements=["obra_id"],
            set_={**actualizar, "plazo_pct": consulta.excluded.plazo_pct, "actualizado_en": ahora},
        ).returning(_INSERTADA)
        return bool((await self._c.execute(upsert)).scalar_one())

    async def bloquear_obra(self, obra_id: str) -> ObraResumen | None:
        t = rpt_obra_resumen.c
        fila = (
            await self._c.execute(
                select(t.obra_id, t.estado, t.monto_contrato, t.cliente_id, t.zona, t.fecha_inicio, t.plazo_dias)
                .where(t.obra_id == obra_id)
                .with_for_update()
            )
        ).first()
        if fila is None:
            return None
        return ObraResumen(
            obra_id=str(fila.obra_id),
            estado=fila.estado,
            monto_contrato=fila.monto_contrato,
            cliente_id=_texto(fila.cliente_id),
            zona=fila.zona,
            fecha_inicio=fila.fecha_inicio,
            plazo_dias=fila.plazo_dias,
        )

    async def marcar_en_ejecucion(self, obra_id: str, ahora: datetime) -> bool:
        resultado = await self._c.execute(
            update(rpt_obra_resumen)
            .where(rpt_obra_resumen.c.obra_id == obra_id)
            .values(estado=EN_EJECUCION, actualizado_en=ahora)
            .returning(rpt_obra_resumen.c.obra_id)
        )
        return resultado.first() is not None

    async def actualizar_indicadores(
        self, obra_id: str, ahora: datetime, avance_pct: Decimal | None = None, plazo_pct: Decimal | None = None
    ) -> None:
        valores: dict[str, Any] = {"actualizado_en": ahora}
        if avance_pct is not None:
            valores["avance_pct"] = avance_pct
        if plazo_pct is not None:
            valores["plazo_pct"] = plazo_pct
        await self._c.execute(update(rpt_obra_resumen).where(rpt_obra_resumen.c.obra_id == obra_id).values(**valores))

    async def obras_para_plazo(self) -> list[PlazoObra]:
        t = rpt_obra_resumen.c
        filas = await self._c.execute(select(t.obra_id, t.fecha_inicio, t.plazo_dias).order_by(t.obra_id))
        return [PlazoObra(str(f.obra_id), f.fecha_inicio, f.plazo_dias) for f in filas]

    # rpt_planilla

    async def obtener_planilla(self, planilla_id: str) -> PlanillaGuardada | None:
        t = rpt_planilla.c
        fila = (
            await self._c.execute(
                select(t.planilla_id, t.estado, t.cargada_en).where(t.planilla_id == planilla_id).with_for_update()
            )
        ).first()
        if fila is None:
            return None
        return PlanillaGuardada(planilla_id=str(fila.planilla_id), estado=fila.estado, cargada_en=fila.cargada_en)

    async def guardar_planilla_aprobada(
        self, planilla: PlanillaAprobadaLeida, monto_periodo: Decimal, avance_periodo_pct: Decimal, ahora: datetime
    ) -> None:
        consulta = insert(rpt_planilla).values(
            planilla_id=planilla.planilla_id,
            obra_id=planilla.obra_id,
            periodo=planilla.periodo,
            estado=APROBADA,
            neto=planilla.neto,
            monto_periodo=monto_periodo,
            avance_periodo_pct=avance_periodo_pct,
            aprobada_en=planilla.aprobada_en,
            actualizado_en=ahora,
        )
        consulta = consulta.on_conflict_do_update(
            index_elements=["planilla_id"],
            set_={
                "obra_id": consulta.excluded.obra_id,
                "periodo": consulta.excluded.periodo,
                "estado": APROBADA,
                "neto": consulta.excluded.neto,
                "monto_periodo": consulta.excluded.monto_periodo,
                "avance_periodo_pct": consulta.excluded.avance_periodo_pct,
                "aprobada_en": func.coalesce(consulta.excluded.aprobada_en, rpt_planilla.c.aprobada_en),
                "actualizado_en": ahora,
            },
        )
        await self._c.execute(consulta)

    async def registrar_observacion(
        self, planilla_id: str, obra_id: str, motivo: str | None, origen: str | None, ahora: datetime
    ) -> None:
        consulta = insert(rpt_planilla).values(
            planilla_id=planilla_id,
            obra_id=obra_id,
            estado=OBSERVADA,
            veces_observada=1,
            ultimo_motivo=motivo,
            ultimo_origen=origen,
            actualizado_en=ahora,
        )
        consulta = consulta.on_conflict_do_update(
            index_elements=["planilla_id"],
            set_={
                "estado": case((rpt_planilla.c.estado == APROBADA, APROBADA), else_=OBSERVADA),
                "veces_observada": rpt_planilla.c.veces_observada + 1,
                "ultimo_motivo": consulta.excluded.ultimo_motivo,
                "ultimo_origen": consulta.excluded.ultimo_origen,
                "actualizado_en": ahora,
            },
        )
        await self._c.execute(consulta)

    async def insertar_planilla_si_no_existe(self, fila: FilaPlanilla, ahora: datetime) -> bool:
        resultado = await self._c.execute(
            insert(rpt_planilla)
            .values(
                planilla_id=fila.planilla_id,
                obra_id=fila.obra_id,
                estado=fila.estado,
                periodo=fila.periodo,
                neto=fila.neto,
                monto_periodo=fila.monto_periodo,
                avance_periodo_pct=fila.avance_periodo_pct,
                veces_observada=fila.veces_observada,
                ultimo_motivo=fila.ultimo_motivo,
                cargada_en=fila.cargada_en,
                actualizado_en=ahora,
            )
            .on_conflict_do_nothing(index_elements=["planilla_id"])
            .returning(rpt_planilla.c.planilla_id)
        )
        return resultado.first() is not None

    async def montos_aprobados(self, obra_id: str) -> list[Decimal]:
        t = rpt_planilla.c
        filas = await self._c.execute(
            select(t.monto_periodo).where(t.obra_id == obra_id, t.estado == APROBADA, t.monto_periodo.is_not(None))
        )
        return [fila.monto_periodo for fila in filas]

    # rpt_cobro

    async def completar_cobro(self, cobro: CobroParcial, ahora: datetime) -> bool:
        columnas = ("numero", "obra_id", "planilla_id", "cliente_id", "zona", "monto", "emitido_en", "vence_en")
        consulta = insert(rpt_cobro).values(
            cobro_id=cobro.cobro_id,
            estado=cobro.estado,
            actualizado_en=ahora,
            **{columna: getattr(cobro, columna) for columna in columnas},
        )
        set_: dict[str, Any] = {
            columna: func.coalesce(rpt_cobro.c[columna], consulta.excluded[columna]) for columna in columnas
        }
        set_["estado"] = func.coalesce(consulta.excluded.estado, rpt_cobro.c.estado)
        set_["actualizado_en"] = ahora
        upsert = consulta.on_conflict_do_update(index_elements=["cobro_id"], set_=set_).returning(_INSERTADA)
        return bool((await self._c.execute(upsert)).scalar_one())

    async def cobros_pendientes(self) -> list[CobroPendiente]:
        t = rpt_cobro.c
        filas = await self._c.execute(select(t.cobro_id, t.vence_en).where(t.estado == PENDIENTE).order_by(t.cobro_id))
        return [CobroPendiente(str(f.cobro_id), f.vence_en) for f in filas]

    async def marcar_vencidos(self, cobro_ids: list[str], ahora: datetime) -> int:
        if not cobro_ids:
            return 0
        resultado = await self._c.execute(
            update(rpt_cobro)
            .where(rpt_cobro.c.cobro_id.in_(cobro_ids), rpt_cobro.c.estado == PENDIENTE)
            .values(estado=VENCIDO, actualizado_en=ahora)
            .returning(rpt_cobro.c.cobro_id)
        )
        return len(resultado.all())

    # rpt_movimiento y rpt_consumo_material

    async def insertar_movimiento(self, movimiento: MovimientoLeido) -> bool:
        resultado = await self._c.execute(
            insert(rpt_movimiento)
            .values(
                movimiento_id=movimiento.movimiento_id,
                obra_id=movimiento.obra_id,
                material_id=movimiento.material_id,
                partida_id=movimiento.partida_id,
                tipo=movimiento.tipo,
                cantidad=movimiento.cantidad,
                precio_referencial=movimiento.precio_referencial,
                periodo=movimiento.periodo,
                supera_stock=movimiento.supera_stock,
                registrado_en=movimiento.registrado_en,
            )
            .on_conflict_do_nothing(index_elements=["movimiento_id"])
            .returning(rpt_movimiento.c.movimiento_id)
        )
        return resultado.first() is not None

    async def sumar_consumo(
        self,
        obra_id: str,
        periodo: str,
        material_id: str,
        partida_id: str,
        cantidad: Decimal,
        monto: Decimal,
        ahora: datetime,
    ) -> None:
        t = rpt_consumo_material.c
        consulta = insert(rpt_consumo_material).values(
            obra_id=obra_id,
            periodo=periodo,
            material_id=material_id,
            partida_id=partida_id,
            cantidad=cantidad,
            monto=monto,
            salidas=1,
            actualizado_en=ahora,
        )
        consulta = consulta.on_conflict_do_update(
            index_elements=["obra_id", "periodo", "material_id", "partida_id"],
            set_={
                "cantidad": t.cantidad + consulta.excluded.cantidad,
                "monto": t.monto + consulta.excluded.monto,
                "salidas": t.salidas + 1,
                "actualizado_en": ahora,
            },
        )
        await self._c.execute(consulta)

    # carga_inicial

    async def marcar_cargas_cortadas(self, ahora: datetime) -> int:
        resultado = await self._c.execute(
            update(carga_inicial)
            .where(carga_inicial.c.estado == CARGA_EN_CURSO)
            .values(
                estado=CARGA_FALLIDA,
                terminada_en=ahora,
                error="La corrida se cortó sin terminar (proceso detenido).",
            )
            .returning(carga_inicial.c.carga_id)
        )
        return len(resultado.all())

    async def iniciar_carga(self, carga_id: str, ahora: datetime) -> None:
        await self._c.execute(
            insert(carga_inicial).values(carga_id=carga_id, iniciada_en=ahora, estado=CARGA_EN_CURSO, obras=0, filas={})
        )

    async def terminar_carga(
        self, carga_id: str, estado: str, obras: int, filas: dict[str, int], error: str | None, ahora: datetime
    ) -> None:
        await self._c.execute(
            update(carga_inicial)
            .where(carga_inicial.c.carga_id == carga_id)
            .values(estado=estado, obras=obras, filas=filas, error=error, terminada_en=ahora)
        )

    # Estado

    async def contar_filas(self) -> dict[str, int]:
        tablas = {
            "rptObraResumen": rpt_obra_resumen,
            "rptPlanilla": rpt_planilla,
            "rptConsumoMaterial": rpt_consumo_material,
            "rptMovimiento": rpt_movimiento,
            "rptCobro": rpt_cobro,
        }
        conteos: dict[str, int] = {}
        for nombre, tabla in tablas.items():
            conteos[nombre] = int((await self._c.execute(select(func.count()).select_from(tabla))).scalar_one())
        return conteos

    async def ultimos_eventos(self) -> list[tuple[str, datetime]]:
        t = evento_aplicado.c
        filas = await self._c.execute(select(t.tipo, func.max(t.aplicado_en)).group_by(t.tipo).order_by(t.tipo))
        return [(str(tipo), aplicado_en) for tipo, aplicado_en in filas]

    async def ultima_carga(self) -> CorridaCarga | None:
        t = carga_inicial.c
        fila = (await self._c.execute(select(carga_inicial).order_by(t.iniciada_en.desc()).limit(1))).first()
        if fila is None:
            return None
        return CorridaCarga(
            carga_id=str(fila.carga_id),
            estado=fila.estado,
            iniciada_en=fila.iniciada_en,
            terminada_en=fila.terminada_en,
            obras=fila.obras,
            filas=dict(fila.filas),
            error=fila.error,
        )


class BaseReportesSql:
    """`BaseReportes` sobre el motor async de `reportes_db`."""

    def __init__(self, motor: AsyncEngine) -> None:
        self.motor = motor

    @asynccontextmanager
    async def transaccion(self) -> AsyncIterator[RepositorioReportesSql]:
        async with self.motor.begin() as conexion:
            yield RepositorioReportesSql(conexion)

    @asynccontextmanager
    async def bloqueo_carga(self) -> AsyncIterator[bool]:
        """Bloqueo de sesión en una conexión propia: se libera al salir (o si el proceso muere)."""
        async with self.motor.connect() as conexion:
            consulta = text("SELECT pg_try_advisory_lock(:clave)")
            tomado = bool((await conexion.execute(consulta, {"clave": CLAVE_BLOQUEO_CARGA})).scalar_one())
            await conexion.commit()
            try:
                yield tomado
            finally:
                if tomado:
                    await conexion.execute(text("SELECT pg_advisory_unlock(:clave)"), {"clave": CLAVE_BLOQUEO_CARGA})
                    await conexion.commit()


def _texto(valor: object) -> str | None:
    return None if valor is None else str(valor)
