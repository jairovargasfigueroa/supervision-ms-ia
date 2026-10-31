"""Carga inicial (criterios 17 a 22): llena el modelo de lectura con lo que Obras, Materiales y Finanzas ya tienen.

Una sola corrida a la vez; por cada obra lee todo (pasos 3 a 7) y lo escribe en **una transacción** con las mismas
reglas que los eventos, «si no existe», así no choca con el worker (que sigue aplicando eventos mientras tanto).

Aproximaciones (lo que los servicios no exponen; diseño de la 401):
- una planilla observada antes de la carga cuenta como máximo 1 vez y sin origen (Obras guarda solo el último motivo);
- `aprobada_en` queda vacía (Obras no expone la fecha de aprobación);
- el precio de cada movimiento es el precio actual del catálogo (Materiales no guarda el precio por movimiento).
"""

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from ia.comun.tiempo import Reloj
from ia.reportes.aplicacion.lecturas import PlanillaCargada
from ia.reportes.aplicacion.proyecciones import Proyecciones
from ia.reportes.aplicacion.puertos import (
    BaseReportes,
    CobroParcial,
    FalloServicio,
    FilaPlanilla,
    FuenteFinanzas,
    FuenteMateriales,
    FuenteObras,
)
from ia.reportes.dominio import indicadores
from ia.reportes.dominio.valores import (
    APROBADA,
    CARGA_FALLIDA,
    CARGA_TERMINADA,
    EN_EJECUCION,
    ESTADOS_PLANILLA_ABIERTA,
    OBSERVADA,
    PENDIENTE,
    VENCIDO,
)

registro = logging.getLogger("ia.reportes.carga_inicial")

MENSAJE_EN_CURSO = "Ya hay una carga inicial en curso"


class CargaEnCurso(Exception):
    def __init__(self) -> None:
        super().__init__(MENSAJE_EN_CURSO)


@dataclass
class ResultadoCarga:
    carga_id: str
    estado: str
    obras: int
    filas: dict[str, int]
    error: str | None = None
    advertencias: list[str] = field(default_factory=list)


def _filas_vacias() -> dict[str, int]:
    return {"rptObraResumen": 0, "rptPlanilla": 0, "rptMovimiento": 0, "rptCobro": 0}


class CargaInicial:
    def __init__(
        self,
        base: BaseReportes,
        obras: FuenteObras,
        materiales: FuenteMateriales,
        finanzas: FuenteFinanzas,
        reloj: Reloj,
    ) -> None:
        self._base = base
        self._obras = obras
        self._materiales = materiales
        self._finanzas = finanzas
        self._reloj = reloj
        self._proyecciones = Proyecciones(reloj)

    async def ejecutar(self, carga_id: str) -> ResultadoCarga:
        """Lanza `CargaEnCurso` si otra corrida tiene el bloqueo. Un fallo de un servicio deja la corrida `FALLIDA`
        (lo ya confirmado queda y la siguiente corrida lo completa)."""
        async with self._base.bloqueo_carga() as tomado:
            if not tomado:
                raise CargaEnCurso()
            async with self._base.transaccion() as repo:
                cortadas = await repo.marcar_cargas_cortadas(self._reloj.ahora())
                await repo.iniciar_carga(carga_id, self._reloj.ahora())
            if cortadas:
                registro.warning("Se marcaron FALLIDA %s corridas anteriores que quedaron EN_CURSO.", cortadas)

            resultado = ResultadoCarga(carga_id=carga_id, estado=CARGA_TERMINADA, obras=0, filas=_filas_vacias())
            obra_actual: str | None = None
            try:
                precios = await self._materiales.precios_materiales()
                obra_ids = await self._obras.listar_obras()
                registro.info("Carga inicial %s: %s obras, %s materiales.", carga_id, len(obra_ids), len(precios))
                for obra_id in obra_ids:
                    obra_actual = obra_id
                    await self._cargar_obra(obra_id, precios, resultado)
                    resultado.obras += 1
                obra_actual = None
            except FalloServicio as error:
                resultado.estado = CARGA_FALLIDA
                donde = f" al cargar la obra {obra_actual}" if obra_actual else ""
                resultado.error = f"{error.servicio} falló{donde}: {error.causa}"
            except Exception as error:
                resultado.estado = CARGA_FALLIDA
                donde = f" al cargar la obra {obra_actual}" if obra_actual else ""
                resultado.error = f"Error inesperado{donde}: {type(error).__name__}: {error}"
                registro.exception("La carga inicial %s falló%s.", carga_id, donde)

            async with self._base.transaccion() as repo:
                await repo.terminar_carga(
                    carga_id,
                    resultado.estado,
                    resultado.obras,
                    resultado.filas,
                    resultado.error,
                    self._reloj.ahora(),
                )
        return resultado

    async def _cargar_obra(self, obra_id: str, precios: dict[str, Decimal | None], resultado: ResultadoCarga) -> None:
        # Lee todo antes de abrir la transacción (pasos 3 a 7).
        obra = await self._obras.obtener_obra(obra_id)
        resumenes = await self._obras.listar_planillas(obra_id)
        cargada_en = self._reloj.ahora()  # momento en que se leyó el paso 4
        planillas = [await self._obras.obtener_planilla(p.planilla_id) for p in resumenes]
        cobros = await self._finanzas.listar_cobros(obra_id)
        movimientos = await self._materiales.listar_movimientos(obra_id)

        async with self._base.transaccion() as repo:
            ahora = self._reloj.ahora()
            # 1 y 2. Inserta si no existe o actualiza los datos descriptivos (como ObraRegistrada); el upsert ya deja
            # la fila bloqueada hasta el final de la transacción, y el SELECT … FOR UPDATE la relee.
            if await self._proyecciones.obra_registrada(repo, obra.obra):
                resultado.filas["rptObraResumen"] += 1
            if obra.estado == EN_EJECUCION:
                await repo.marcar_en_ejecucion(obra_id, ahora)
            resumen = await repo.bloquear_obra(obra_id)
            assert resumen is not None

            # 3. Planillas, solo si no existen (lo que ya escribió un evento se respeta).
            for planilla in planillas:
                fila = self._fila_planilla(planilla, resumen.monto_contrato or Decimal(0), cargada_en)
                if fila is not None and await repo.insertar_planilla_si_no_existe(fila, ahora):
                    resultado.filas["rptPlanilla"] += 1

            # 4. Avance desde rpt_planilla (misma regla) y comparación con el de Obras.
            avance = await self._proyecciones.recalcular_avance(repo, resumen)
            if obra.avance_pct is not None and avance != indicadores.redondear(obra.avance_pct):
                advertencia = (
                    f"El avance de la obra {obra_id} no coincide: base de reportes {avance} %, "
                    f"Obras {obra.avance_pct} %."
                )
                registro.warning(advertencia)
                resultado.advertencias.append(advertencia)

            # 5. Cobros (completa columnas vacías; el estado es el de Finanzas).
            for cobro in cobros:
                insertado = await repo.completar_cobro(
                    CobroParcial(
                        cobro_id=cobro.cobro_id,
                        numero=cobro.numero,
                        obra_id=obra_id,
                        planilla_id=cobro.planilla_id,
                        cliente_id=resumen.cliente_id,
                        zona=resumen.zona,
                        monto=cobro.monto_neto,
                        emitido_en=cobro.emitido_en,
                        vence_en=cobro.vence_en,
                        estado=cobro.estado if cobro.estado in (PENDIENTE, VENCIDO) else None,
                    ),
                    ahora,
                )
                if insertado:
                    resultado.filas["rptCobro"] += 1

            # 6. Movimientos con el precio actual del catálogo; solo los que se insertan suman en el consumo.
            for movimiento in movimientos:
                con_precio = replace(movimiento, precio_referencial=precios.get(movimiento.material_id))
                if await self._proyecciones.movimiento_registrado(repo, con_precio):
                    resultado.filas["rptMovimiento"] += 1

    @staticmethod
    def _fila_planilla(planilla: PlanillaCargada, monto_contrato: Decimal, cargada_en: datetime) -> FilaPlanilla | None:
        observada = planilla.observacion is not None
        if planilla.estado == APROBADA:
            monto = indicadores.monto_periodo(planilla.montos_lineas)
            return FilaPlanilla(
                planilla_id=planilla.planilla_id,
                obra_id=planilla.obra_id,
                estado=APROBADA,
                periodo=planilla.periodo,
                neto=planilla.neto,
                monto_periodo=monto,
                avance_periodo_pct=indicadores.avance_periodo_pct(monto, monto_contrato),
                veces_observada=1 if observada else 0,
                ultimo_motivo=planilla.observacion,
                cargada_en=cargada_en,
            )
        if planilla.estado == OBSERVADA or (planilla.estado in ESTADOS_PLANILLA_ABIERTA and observada):
            # Como la dejarían los eventos (PlanillaObservada): sin periodo ni montos.
            return FilaPlanilla(
                planilla_id=planilla.planilla_id,
                obra_id=planilla.obra_id,
                estado=OBSERVADA,
                periodo=None,
                neto=None,
                monto_periodo=None,
                avance_periodo_pct=None,
                veces_observada=1 if observada else 0,
                ultimo_motivo=planilla.observacion,
                cargada_en=cargada_en,
            )
        return None
