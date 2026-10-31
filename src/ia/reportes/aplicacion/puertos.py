"""Puertos de `reportes`: la base de reportes (y, en la carga inicial, los servicios que lee)."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from ia.reportes.aplicacion.lecturas import (
    CobroCargado,
    MovimientoLeido,
    ObraCargada,
    ObraLeida,
    PlanillaAprobadaLeida,
    PlanillaCargada,
    PlanillaResumenCargada,
)

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


@dataclass(frozen=True)
class FilaPlanilla:
    """Planilla que la carga inicial inserta solo si no existe."""

    planilla_id: str
    obra_id: str
    estado: str
    periodo: str | None
    neto: Decimal | None
    monto_periodo: Decimal | None
    avance_periodo_pct: Decimal | None
    veces_observada: int
    ultimo_motivo: str | None
    cargada_en: datetime


@dataclass(frozen=True)
class CorridaCarga:
    carga_id: str
    estado: str
    iniciada_en: datetime
    terminada_en: datetime | None
    obras: int
    filas: dict[str, int]
    error: str | None


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

    async def insertar_planilla_si_no_existe(self, fila: FilaPlanilla, ahora: datetime) -> bool:
        """`ON CONFLICT DO NOTHING` (lo que ya escribió un evento se respeta). `True` si insertó."""
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

    # carga_inicial
    async def marcar_cargas_cortadas(self, ahora: datetime) -> int:
        """Las corridas que quedaron `EN_CURSO` (proceso detenido) pasan a `FALLIDA`."""
        ...

    async def iniciar_carga(self, carga_id: str, ahora: datetime) -> None: ...

    async def terminar_carga(
        self, carga_id: str, estado: str, obras: int, filas: dict[str, int], error: str | None, ahora: datetime
    ) -> None: ...

    # Estado (GET /reportes/estado)
    async def contar_filas(self) -> dict[str, int]:
        """Filas de cada tabla `rpt_*` con su nombre en camelCase (`rptObraResumen`…)."""
        ...

    async def ultimos_eventos(self) -> list[tuple[str, datetime]]:
        """Último `aplicado_en` de cada tipo de evento."""
        ...

    async def ultima_carga(self) -> CorridaCarga | None: ...


class BaseReportes(Protocol):
    def transaccion(self) -> AbstractAsyncContextManager[RepositorioReportes]:
        """Abre una transacción; se confirma al salir sin error y se deshace si hay una excepción."""
        ...

    def bloqueo_carga(self) -> AbstractAsyncContextManager[bool]:
        """Una sola carga inicial a la vez (`pg_try_advisory_lock`): `False` si ya la tiene otra corrida."""
        ...


# Servicios que lee la carga inicial (solo GET).


class FalloServicio(Exception):
    """El servicio no respondió, respondió con error o devolvió datos inválidos (después del reintento)."""

    def __init__(self, servicio: str, causa: str) -> None:
        super().__init__(f"{servicio}: {causa}")
        self.servicio = servicio
        self.causa = causa


class FuenteObras(Protocol):
    async def listar_obras(self) -> list[str]:
        """Paso 2: ids de todas las obras."""
        ...

    async def obtener_obra(self, obra_id: str) -> ObraCargada:
        """Paso 3."""
        ...

    async def listar_planillas(self, obra_id: str) -> list[PlanillaResumenCargada]:
        """Paso 4."""
        ...

    async def obtener_planilla(self, planilla_id: str) -> PlanillaCargada:
        """Paso 5."""
        ...


class FuenteMateriales(Protocol):
    async def precios_materiales(self) -> dict[str, Decimal | None]:
        """Paso 1: precio referencial actual de cada material del catálogo."""
        ...

    async def listar_movimientos(self, obra_id: str) -> list[MovimientoLeido]:
        """Paso 7: todos los movimientos de la obra, sin precio (Materiales no lo guarda por movimiento); vacío si
        Materiales responde `404 OBRA_NO_ENCONTRADA` (obra que no está en `obra_acceso`)."""
        ...


class FuenteFinanzas(Protocol):
    async def listar_cobros(self, obra_id: str) -> list[CobroCargado]:
        """Paso 6."""
        ...
