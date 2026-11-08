"""Casos de uso de `ia-api` para los agentes (502): consultas con visibilidad, «Ejecutar ahora» y la decisión humana.

Aprobar **solo registra** la decisión: no se crea, modifica ni envía nada en otro servicio (la «tarea interna» es la
propia sugerencia `APROBADA`). Rechazar exige un motivo de 10 a 500 caracteres. Una sugerencia decidida no se vuelve a
decidir (actualización atómica solo si sigue `PENDIENTE`).
"""

from dataclasses import dataclass

from ia.agentes.aplicacion.puertos import ObrasDelResidente, RepositorioEjecuciones, RepositorioSugerencias
from ia.agentes.aplicacion.visibilidad import alcance_de
from ia.agentes.dominio.definiciones import AGENTES, DefinicionAgente
from ia.agentes.dominio.ejecucion import (
    APROBADA,
    COMENTARIO_MAXIMO,
    MANUAL,
    MOTIVO_MAXIMO,
    MOTIVO_MINIMO,
    PENDIENTE,
    RECHAZADA,
    Ejecucion,
    HallazgoGuardado,
    Sugerencia,
)
from ia.auth.usuario import Usuario
from ia.comun.tiempo import Reloj


class AgenteInvalido(Exception):
    pass


class EjecucionNoEncontrada(Exception):
    pass


class SugerenciaNoEncontrada(Exception):
    pass


class SugerenciaYaDecidida(Exception):
    pass


class TextoInvalido(Exception):
    def __init__(self, campo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.campo = campo
        self.mensaje = mensaje


@dataclass(frozen=True)
class InfoAgente:
    definicion: DefinicionAgente
    ultima: Ejecucion | None
    pendientes: int
    hallazgos_visibles: int
    """Hallazgos de la última ejecución en las obras que ve el usuario (el residente, solo los de sus obras)."""


@dataclass(frozen=True)
class HallazgoVigente:
    ejecucion: Ejecucion
    hallazgo: HallazgoGuardado


class CasosAgentes:
    def __init__(
        self,
        ejecuciones: RepositorioEjecuciones,
        sugerencias: RepositorioSugerencias,
        obras: ObrasDelResidente,
        reloj: Reloj,
        proveedor_llm: str,
        modelo: str,
    ) -> None:
        self._ejecuciones = ejecuciones
        self._sugerencias = sugerencias
        self._obras = obras
        self._reloj = reloj
        self._proveedor = proveedor_llm
        self._modelo = modelo

    async def agentes(self, usuario: Usuario) -> list[InfoAgente]:
        alcance = await alcance_de(usuario, self._obras)
        obra_ids = None if alcance.obra_ids is None else sorted(alcance.obra_ids)
        informacion = []
        for agente in alcance.agentes:
            ultima = await self._ejecuciones.ultima(agente)
            visibles = 0 if ultima is None else sum(1 for h in ultima.hallazgos if alcance.ve_obra(h.obra_id))
            pendientes = await self._sugerencias.contar_pendientes(agente, obra_ids)
            informacion.append(InfoAgente(AGENTES[agente], ultima, pendientes, visibles))
        return informacion

    async def ejecutar_ahora(self, usuario: Usuario, agente: str) -> Ejecucion:
        """`AgenteInvalido` o `EjecucionEnCurso`; si no, la ejecución `MANUAL` `EN_CURSO` (la toma `ia-worker`)."""
        if agente not in AGENTES:
            raise AgenteInvalido(agente)
        ejecucion = Ejecucion.nueva(agente, MANUAL, usuario.id, self._reloj.ahora(), self._proveedor, self._modelo)
        await self._ejecuciones.crear(ejecucion)
        return ejecucion

    async def ejecuciones(self, agente: str | None, estado: str | None, limite: int) -> list[Ejecucion]:
        return await self._ejecuciones.listar(agente, estado, limite)

    async def ejecucion(self, ejecucion_id: str) -> Ejecucion:
        ejecucion = await self._ejecuciones.obtener(ejecucion_id)
        if ejecucion is None:
            raise EjecucionNoEncontrada(ejecucion_id)
        return ejecucion

    async def hallazgos(self, usuario: Usuario, agente: str | None, obra_id: str | None) -> list[HallazgoVigente]:
        """Los de la última ejecución `TERMINADA` de cada agente visible."""
        alcance = await alcance_de(usuario, self._obras)
        vigentes: list[HallazgoVigente] = []
        for nombre in alcance.agentes:
            if agente is not None and nombre != agente:
                continue
            ultima = await self._ejecuciones.ultima_terminada(nombre)
            if ultima is None:
                continue
            vigentes += [
                HallazgoVigente(ultima, h)
                for h in ultima.hallazgos
                if alcance.ve_obra(h.obra_id) and (obra_id is None or h.obra_id == obra_id)
            ]
        return vigentes

    async def sugerencias(
        self, usuario: Usuario, estado: str | None, agente: str | None, obra_id: str | None, limite: int
    ) -> list[Sugerencia]:
        alcance = await alcance_de(usuario, self._obras)
        agentes = [a for a in alcance.agentes if agente is None or a == agente]
        if not agentes:
            return []
        if obra_id is not None:
            if not alcance.ve_obra(obra_id):
                return []
            obra_ids: list[str] | None = [obra_id]
        else:
            obra_ids = None if alcance.obra_ids is None else sorted(alcance.obra_ids)
        return await self._sugerencias.listar(estado, agentes, obra_ids, limite)

    async def aprobar(self, usuario: Usuario, sugerencia_id: str, comentario: str | None) -> Sugerencia:
        await self._visible(usuario, sugerencia_id)
        texto = (comentario or "").strip() or None
        if texto is not None and len(texto) > COMENTARIO_MAXIMO:
            raise TextoInvalido("comentario", f"El comentario puede tener hasta {COMENTARIO_MAXIMO} caracteres.")
        return await self._decidir(usuario, sugerencia_id, APROBADA, texto)

    async def rechazar(self, usuario: Usuario, sugerencia_id: str, motivo: str | None) -> Sugerencia:
        await self._visible(usuario, sugerencia_id)
        texto = (motivo or "").strip()
        if not MOTIVO_MINIMO <= len(texto) <= MOTIVO_MAXIMO:
            raise TextoInvalido("motivo", f"El motivo debe tener entre {MOTIVO_MINIMO} y {MOTIVO_MAXIMO} caracteres.")
        return await self._decidir(usuario, sugerencia_id, RECHAZADA, texto)

    async def _visible(self, usuario: Usuario, sugerencia_id: str) -> Sugerencia:
        sugerencia = await self._sugerencias.obtener(sugerencia_id)
        if sugerencia is None or not (await alcance_de(usuario, self._obras)).ve_sugerencia(sugerencia):
            raise SugerenciaNoEncontrada(sugerencia_id)
        return sugerencia

    async def _decidir(self, usuario: Usuario, sugerencia_id: str, estado: str, comentario: str | None) -> Sugerencia:
        decidida = await self._sugerencias.decidir(sugerencia_id, estado, usuario.id, self._reloj.ahora(), comentario)
        if decidida is None:
            raise SugerenciaYaDecidida(sugerencia_id)
        assert decidida.estado != PENDIENTE
        return decidida
