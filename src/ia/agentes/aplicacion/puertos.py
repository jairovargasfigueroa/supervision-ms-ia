"""Puertos de los agentes (502): `ia_db` (ejecuciones y sugerencias), los servidores MCP y las obras del residente."""

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Protocol

from ia.agentes.dominio.ejecucion import Ejecucion, ErrorEjecucion, HallazgoGuardado, Paso, Sugerencia
from ia.mcp.cliente.cliente import HerramientaPublicada, ResultadoHerramienta


class EjecucionEnCurso(Exception):
    """Ya hay una ejecución `EN_CURSO` del mismo agente (409 EJECUCION_EN_CURSO)."""


class RepositorioEjecuciones(Protocol):
    """`ia_db.ejecucion_agente` (también es la cola de `ia-worker`)."""

    async def crear(self, ejecucion: Ejecucion) -> None:
        """`EjecucionEnCurso` si ya hay una `EN_CURSO` del mismo agente (índice único parcial: atómico)."""
        ...

    async def tomar_siguiente(self, ahora: datetime) -> Ejecucion | None:
        """La `EN_CURSO` más antigua sin `tomada_en`, marcándola (`find_one_and_update`: dos workers no toman la
        misma)."""
        ...

    async def agregar_paso(self, ejecucion_id: str, paso: Paso) -> None: ...

    async def terminar(
        self, ejecucion_id: str, resumen: str, hallazgos: Sequence[HallazgoGuardado], en: datetime
    ) -> None: ...

    async def fallar(self, ejecucion_id: str, error: ErrorEjecucion, en: datetime) -> None: ...

    async def recuperar_interrumpidas(self, iniciadas_antes_de: datetime, ahora: datetime) -> int:
        """Las `EN_CURSO` iniciadas antes de `iniciadas_antes_de` pasan a `FALLIDA` con `INTERRUMPIDA`."""
        ...

    async def obtener(self, ejecucion_id: str) -> Ejecucion | None: ...

    async def listar(self, agente: str | None, estado: str | None, limite: int) -> list[Ejecucion]:
        """Más recientes primero (sin pasos ni hallazgos completos no hace falta distinguir: se leen enteras)."""
        ...

    async def ultima(self, agente: str) -> Ejecucion | None:
        """La última ejecución del agente (cualquier estado)."""
        ...

    async def ultima_terminada(self, agente: str) -> Ejecucion | None: ...

    async def hay_programada_desde(self, agente: str, desde: datetime) -> bool:
        """Si ya existe una ejecución `PROGRAMADA` del agente con `iniciada_en` desde `desde`."""
        ...


class RepositorioSugerencias(Protocol):
    """`ia_db.sugerencia`."""

    async def crear(self, sugerencia: Sugerencia) -> bool:
        """`False` si ya hay una `PENDIENTE` con la misma obra y acción (índice único parcial)."""
        ...

    async def obtener(self, sugerencia_id: str) -> Sugerencia | None: ...

    async def listar(
        self,
        estado: str | None,
        agentes: Sequence[str],
        obra_ids: Sequence[str] | None,
        limite: int,
    ) -> list[Sugerencia]:
        """De los `agentes` dados; `obra_ids = None` = todas las obras. Más recientes primero."""
        ...

    async def contar_pendientes(self, agente: str, obra_ids: Sequence[str] | None) -> int: ...

    async def decidir(
        self, sugerencia_id: str, estado: str, por: str, en: datetime, comentario: str | None
    ) -> Sugerencia | None:
        """`update_one({_id, estado: PENDIENTE}, …)`: `None` si ya no estaba pendiente."""
        ...


class SesionMcp(Protocol):
    """Los servidores MCP que usa una ejecución, con el token de `ia-agentes` y `X-Request-Id` = id de la ejecución."""

    async def listar(self, servidor: str) -> list[HerramientaPublicada]:
        """`FalloMcp` si el servidor no responde."""
        ...

    async def llamar(self, servidor: str, herramienta: str, argumentos: dict[str, Any]) -> ResultadoHerramienta:
        """`FalloMcp` si el servidor no responde (10 s)."""
        ...


class ConectorMcp(Protocol):
    def sesion(self, ejecucion_id: str) -> SesionMcp: ...


class ObrasDelResidente(Protocol):
    """Visibilidad de la 401: obras con `rpt_obra_resumen.residente_id = sub`."""

    async def obras_del_residente(self, residente_id: str) -> list[str]: ...
