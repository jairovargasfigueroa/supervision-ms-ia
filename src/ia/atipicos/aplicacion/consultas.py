"""Lecturas de consumos atípicos para el gateway (403): lista por obra y pendientes por obra (tablero)."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ia.atipicos.aplicacion.errores import DatosInvalidos, DemasiadasObras, ObraNoEncontrada
from ia.atipicos.aplicacion.puertos import ObrasReportes, RepositorioAtipicos
from ia.atipicos.dominio.consumo import ConsumoAtipico
from ia.atipicos.dominio.valores import ESTADOS_REVISION, MAXIMO_LISTA, MAXIMO_OBRAS_RESUMEN
from ia.auth.usuario import Usuario
from ia.auth.visibilidad import puede_ver_obra, ve_todas_las_obras


@dataclass(frozen=True)
class ConsumoConObra:
    consumo: ConsumoAtipico
    obra_nombre: str | None


@dataclass(frozen=True)
class ConsumosFiltrados:
    consumos: list[ConsumoConObra]
    total: int


class ConsultarConsumosAtipicos:
    def __init__(self, repositorio: RepositorioAtipicos, obras: ObrasReportes, zona: str = "America/La_Paz") -> None:
        self._repositorio = repositorio
        self._obras = obras
        self._zona = ZoneInfo(zona)

    async def listar(
        self,
        usuario: Usuario,
        obra_id: str | None = None,
        estado: str | None = None,
        desde: str | None = None,
        hasta: str | None = None,
    ) -> list[ConsumoAtipico]:
        """Hasta 200, de la más reciente a la más antigua. Sin `obra_id`, todas las obras visibles."""
        if estado is not None and estado not in ESTADOS_REVISION:
            raise DatosInvalidos("El estado debe ser PENDIENTE, NORMAL o CONFIRMADO.")
        dia_desde, dia_hasta = _fecha(desde, "desde"), _fecha(hasta, "hasta")
        if dia_desde and dia_hasta and dia_desde > dia_hasta:
            raise DatosInvalidos("La fecha «desde» es posterior a «hasta».")

        obra_ids: list[str] | None
        if obra_id is not None:
            obra = (await self._obras.obras([obra_id])).get(obra_id)
            if obra is None or not puede_ver_obra(usuario, obra.residente_id):
                raise ObraNoEncontrada("Obra no encontrada")
            obra_ids = [obra_id]
        elif ve_todas_las_obras(usuario):
            obra_ids = None
        else:
            obra_ids = await self._obras.obras_del_residente(usuario.id)
            if not obra_ids:
                return []

        # Fechas del negocio (La Paz): `desde` desde las 00:00 y `hasta` incluido entero.
        inicio = datetime.combine(dia_desde, time(), self._zona) if dia_desde else None
        fin = datetime.combine(dia_hasta + timedelta(days=1), time(), self._zona) if dia_hasta else None
        return await self._repositorio.listar(obra_ids, estado, inicio, fin, MAXIMO_LISTA)

    async def para_agentes(
        self, obra_id: str | None, estados: list[str], dias: int, hoy: date, limite: int
    ) -> ConsumosFiltrados:
        """Herramienta MCP `consumos_atipicos` (501; `agente_ia` ve todas las obras). Solo lee.

        `registrado_en` desde las 00:00 (La Paz) de `hoy − dias`; del puntaje más alto al más bajo.
        `ObraNoEncontrada` si `obra_id` no está en la base de reportes.
        """
        if obra_id is not None and obra_id not in await self._obras.obras([obra_id]):
            raise ObraNoEncontrada("Obra no encontrada")
        desde = datetime.combine(hoy - timedelta(days=dias), time(), self._zona)
        consumos, total = await self._repositorio.buscar(
            None if obra_id is None else [obra_id], list(dict.fromkeys(estados)), desde, limite
        )
        obras = await self._obras.obras(sorted({c.obra_id for c in consumos}))
        return ConsumosFiltrados(
            [ConsumoConObra(c, obras[c.obra_id].nombre if c.obra_id in obras else None) for c in consumos], total
        )

    async def resumen(self, usuario: Usuario, obra_ids: list[str]) -> list[tuple[str, int]]:
        """`PENDIENTE` de cada obra pedida y visible (0 si no tiene), en el orden pedido."""
        pedidas = list(dict.fromkeys(obra_ids))
        if len(pedidas) > MAXIMO_OBRAS_RESUMEN:
            raise DemasiadasObras(f"Se pueden pedir hasta {MAXIMO_OBRAS_RESUMEN} obras por llamada.")
        conocidas = await self._obras.obras(pedidas)
        visibles = [
            obra_id
            for obra_id in pedidas
            if obra_id in conocidas and puede_ver_obra(usuario, conocidas[obra_id].residente_id)
        ]
        if not visibles:
            return []
        pendientes = await self._repositorio.pendientes_por_obra(visibles)
        return [(obra_id, pendientes.get(obra_id, 0)) for obra_id in visibles]


def _fecha(texto: str | None, campo: str) -> date | None:
    if texto is None:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError as error:
        raise DatosInvalidos(f"La fecha «{campo}» debe tener el formato AAAA-MM-DD.") from error
