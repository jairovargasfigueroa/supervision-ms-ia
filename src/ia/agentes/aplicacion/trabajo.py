"""Trabajo de `ia-worker` con los agentes (502): la cola en `ia_db.ejecucion_agente`, la ejecución diaria y la
recuperación al iniciar.

- Cola: toma la `EN_CURSO` más antigua sin `tomada_en` (`find_one_and_update`) y la ejecuta; **una a la vez** (con
  Ollama la VM tiene un solo modelo cargado; con Anthropic se mantiene igual).
- Diaria: encola los tres agentes en orden (`PROGRAMADA`); no repite un agente que ya tiene una ejecución programada
  con `iniciada_en` de hoy (La Paz).
- Al iniciar: las `EN_CURSO` con más de 10 minutos, y toda `EN_CURSO` ya tomada (el único worker que podía tenerla es
  el que arranca), pasan a `FALLIDA` con `INTERRUMPIDA`. En marcha, una vez por minuto, también las tomadas hace más
  de 10 minutos (una ejecución dura 3 como máximo: si sigue `EN_CURSO`, no se pudo guardar su final).
"""

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from ia.agentes.aplicacion.ejecutor import EjecutorAgente
from ia.agentes.aplicacion.puertos import EjecucionEnCurso, RepositorioEjecuciones
from ia.agentes.dominio.definiciones import AGENTES
from ia.agentes.dominio.ejecucion import PROGRAMADA, Ejecucion
from ia.comun.tiempo import ZONA_NEGOCIO, Reloj

registro = logging.getLogger("ia.agentes")

INTERRUMPIDA_DESPUES = timedelta(minutes=10)
REVISION_COLGADAS = timedelta(minutes=1)


class TrabajoAgentes:
    def __init__(
        self,
        ejecuciones: RepositorioEjecuciones,
        ejecutor: EjecutorAgente,
        reloj: Reloj,
        proveedor_llm: str,
        modelo: str,
        zona: str = ZONA_NEGOCIO,
    ) -> None:
        self._ejecuciones = ejecuciones
        self._ejecutor = ejecutor
        self._reloj = reloj
        self._proveedor = proveedor_llm
        self._modelo = modelo
        self._zona = ZoneInfo(zona)
        self._ultima_revision: datetime | None = None

    async def recuperar(self) -> int:
        """Al iniciar el worker."""
        ahora = self._reloj.ahora()
        cantidad = await self._ejecuciones.recuperar_interrumpidas(
            ahora, iniciadas_antes_de=ahora - INTERRUMPIDA_DESPUES, tomadas_antes_de=ahora
        )
        if cantidad:
            registro.warning("%s ejecuciones de agentes quedaron interrumpidas al reiniciar.", cantidad)
        return cantidad

    async def recuperar_colgadas(self) -> int:
        """Con el worker en marcha: las tomadas hace más de 10 minutos."""
        ahora = self._reloj.ahora()
        self._ultima_revision = ahora
        cantidad = await self._ejecuciones.recuperar_interrumpidas(ahora, tomadas_antes_de=ahora - INTERRUMPIDA_DESPUES)
        if cantidad:
            registro.warning("%s ejecuciones de agentes quedaron colgadas y se marcaron interrumpidas.", cantidad)
        return cantidad

    async def procesar_siguiente(self) -> bool:
        """Toma y ejecuta la siguiente ejecución en espera. `False` si no había ninguna."""
        if self._ultima_revision is None or self._reloj.ahora() - self._ultima_revision >= REVISION_COLGADAS:
            await self.recuperar_colgadas()
        ejecucion = await self._ejecuciones.tomar_siguiente(self._reloj.ahora())
        if ejecucion is None:
            return False
        registro.info("Ejecutando el agente %s (%s).", ejecucion.agente, ejecucion.id)
        await self._ejecutor.ejecutar(ejecucion)
        return True

    async def encolar_diarios(self) -> list[str]:
        """Encola los tres agentes (en orden) que todavía no tienen su ejecución programada de hoy."""
        desde = datetime.combine(self._reloj.hoy(), time(), self._zona)
        encolados = []
        for agente in AGENTES:
            if await self._ejecuciones.hay_programada_desde(agente, desde):
                continue
            ejecucion = Ejecucion.nueva(agente, PROGRAMADA, None, self._reloj.ahora(), self._proveedor, self._modelo)
            try:
                await self._ejecuciones.crear(ejecucion)
            except EjecucionEnCurso:
                registro.info("El agente %s ya se está ejecutando: no se encola la ejecución diaria.", agente)
                continue
            encolados.append(agente)
        return encolados
