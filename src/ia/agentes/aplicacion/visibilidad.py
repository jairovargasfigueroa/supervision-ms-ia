"""Visibilidad de los agentes (502) con la regla de la 401 (`src/ia/auth`).

- `admin` y `gerente`: los tres agentes y todas las obras.
- `residente`: solo sus obras (`rpt_obra_resumen.residente_id = sub`) y nunca Control de cobros.
"""

from dataclasses import dataclass

from ia.agentes.aplicacion.puertos import ObrasDelResidente
from ia.agentes.dominio.definiciones import AGENTES, AGENTES_DEL_RESIDENTE
from ia.agentes.dominio.ejecucion import Sugerencia
from ia.auth.usuario import Usuario
from ia.auth.visibilidad import residente_filtrado


@dataclass(frozen=True)
class Alcance:
    agentes: tuple[str, ...]
    obra_ids: frozenset[str] | None
    """`None` = todas las obras."""

    def ve_agente(self, agente: str) -> bool:
        return agente in self.agentes

    def ve_obra(self, obra_id: str) -> bool:
        return self.obra_ids is None or obra_id in self.obra_ids

    def ve_sugerencia(self, sugerencia: Sugerencia) -> bool:
        return self.ve_agente(sugerencia.agente) and self.ve_obra(sugerencia.obra_id)


async def alcance_de(usuario: Usuario, obras: ObrasDelResidente) -> Alcance:
    residente = residente_filtrado(usuario)
    if residente is None:
        return Alcance(tuple(AGENTES), None)
    return Alcance(AGENTES_DEL_RESIDENTE, frozenset(await obras.obras_del_residente(residente)))
