"""Puertos de `reportes`: la base de reportes (y, en la carga inicial, los servicios que lee)."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from ia.reportes.aplicacion.lecturas import MovimientoLeido, ObraLeida, PlanillaAprobadaLeida

# Lo que devuelve la base.


@dataclass(frozen=True)
class ObraResumen:
    obra_id: str
    estado: str
    monto_contrato: Decimal | None
    cliente_id: str | None
    zona: str | None
    fecha_inicio: date | None
    plazo_dias: int | None


@dataclass(frozen=True)
class PlanillaGuardada:
    planilla_id: str
    estado: str
    cargada_en: datetime | None


@dataclass(frozen=True)
class CobroParcial:
    """Columnas de `rpt_cobro` que trae cada camino. Las `None` no se tocan (se completan con COALESCE), salvo
    `estado`, que si viene reemplaza al anterior."""

    cobro_id: str
    numero: str | None = None
    obra_id: str | None = None
    planilla_id: str | None = None
    cliente_id: str | None = None
    zona: str | None = None
    monto: Decimal | None = None
    emitido_en: date | None = None
    vence_en: date | None = None
    estado: str | None = None


@dataclass(frozen=True)
class CobroPendiente:
    cobro_id: str
    vence_en: date | None


@dataclass(frozen=True)
class PlazoObra:
    obra_id: str
    fecha_inicio: date | None
    plazo_dias: int | None


class RepositorioReportes(Protocol):
    """Operaciones sobre `reportes_db` dentro de una transacción abierta. Cada escritura es idempotente por fila."""

    # rpt_obra_resumen
    async def guardar_obra(self, obra: ObraLeida, plazo_pct: Decimal, ahora: datetime) -> bool:
        """Inserta (`SIN_CONTRATO`, avance 0) o, si existe, completa y actualiza los datos descriptivos (nunca el
        estado). `True` si insertó."""
        ...

    async def bloquear_obra(self, obra_id: str) -> ObraResumen | None:
        """`SELECT … FOR UPDATE` de la fila de la obra (o `None` si no existe)."""
        ...

    async def marcar_en_ejecucion(self, obra_id: str, ahora: datetime) -> bool:
        """`False` si la obra no existe."""
        ...

    async def actualizar_indicadores(
        self, obra_id: str, ahora: datetime, avance_pct: Decimal | None = None, plazo_pct: Decimal | None = None
    ) -> None: ...

    async def obras_para_plazo(self) -> list[PlazoObra]: ...

    # rpt_planilla
    async def obtener_planilla(self, planilla_id: str) -> PlanillaGuardada | None:
        """Con bloqueo de la fila (`FOR UPDATE`)."""
        ...

    async def guardar_planilla_aprobada(
        self, planilla: PlanillaAprobadaLeida, monto_periodo: Decimal, avance_periodo_pct: Decimal, ahora: datetime
    ) -> None: ...

    async def registrar_observacion(
        self, planilla_id: str, obra_id: str, motivo: str | None, origen: str | None, ahora: datetime
    ) -> None:
        """`OBSERVADA` (si no estaba `APROBADA`), `veces_observada + 1`, último motivo y origen."""
        ...

    async def montos_aprobados(self, obra_id: str) -> list[Decimal]:
        """`monto_periodo` de las planillas `APROBADA` de la obra."""
        ...

    # rpt_cobro
    async def completar_cobro(self, cobro: CobroParcial, ahora: datetime) -> bool:
        """Inserta o completa las columnas vacías. `True` si insertó."""
        ...

    async def cobros_pendientes(self) -> list[CobroPendiente]: ...

    async def marcar_vencidos(self, cobro_ids: list[str], ahora: datetime) -> int:
        """Los que siguen `PENDIENTE` pasan a `VENCIDO`; devuelve cuántos cambió."""
        ...

    # rpt_movimiento y rpt_consumo_material
    async def insertar_movimiento(self, movimiento: MovimientoLeido) -> bool:
        """`ON CONFLICT DO NOTHING`. `True` solo si insertó."""
        ...

    async def sumar_consumo(
        self,
        obra_id: str,
        periodo: str,
        material_id: str,
        partida_id: str,
        cantidad: Decimal,
        monto: Decimal,
        ahora: datetime,
    ) -> None: ...


class BaseReportes(Protocol):
    def transaccion(self) -> AbstractAsyncContextManager[RepositorioReportes]:
        """Abre una transacción; se confirma al salir sin error y se deshace si hay una excepción."""
        ...
