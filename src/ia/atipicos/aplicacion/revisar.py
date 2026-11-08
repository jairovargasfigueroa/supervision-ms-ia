"""Revisión de un consumo atípico: «Es normal» (`NORMAL`) o «Confirmar problema» (`CONFIRMADO`) (403, criterios 7 a 9).

Solo `admin` y el `residente` asignado a la obra (el rol lo filtra la ruta: `gerente` recibe 403). Queda registrado
quién lo revisó, cuándo y su comentario. Un consumo ya revisado no se puede revisar de nuevo.
"""

from ia.atipicos.aplicacion.errores import (
    ComentarioInvalido,
    ConsumoNoEncontrado,
    DatosInvalidos,
    RevisionYaRegistrada,
)
from ia.atipicos.aplicacion.puertos import ObrasReportes, RepositorioAtipicos
from ia.atipicos.dominio.consumo import ConsumoAtipico
from ia.atipicos.dominio.revision import es_estado_de_revision, normalizar_comentario, regla_comentario_incumplida
from ia.auth.usuario import Usuario
from ia.comun.tiempo import Reloj

NO_ENCONTRADO = "No encontramos ese consumo atípico."


class RevisarConsumoAtipico:
    def __init__(self, repositorio: RepositorioAtipicos, obras: ObrasReportes, reloj: Reloj) -> None:
        self._repositorio = repositorio
        self._obras = obras
        self._reloj = reloj

    async def revisar(self, usuario: Usuario, consumo_id: str, estado: str, comentario: str | None) -> ConsumoAtipico:
        if not es_estado_de_revision(estado):
            raise DatosInvalidos("El estado de la revisión debe ser NORMAL o CONFIRMADO.")
        comentario = normalizar_comentario(comentario)
        regla = regla_comentario_incumplida(estado, comentario)
        if regla:
            raise ComentarioInvalido(regla)

        consumo = await self._repositorio.obtener(consumo_id)
        if consumo is None or not await self._puede_revisar(usuario, consumo):
            raise ConsumoNoEncontrado(NO_ENCONTRADO)
        revisado = await self._repositorio.revisar(consumo_id, estado, usuario.id, self._reloj.ahora(), comentario)
        if revisado is None:
            raise RevisionYaRegistrada("Este consumo ya fue revisado")
        return revisado

    async def _puede_revisar(self, usuario: Usuario, consumo: ConsumoAtipico) -> bool:
        """`admin` revisa cualquiera; si no, solo el residente asignado a la obra (aunque tenga también `gerente`)."""
        if usuario.tiene_alguno("admin"):
            return True
        if "residente" not in usuario.roles:
            return False
        obra = (await self._obras.obras([consumo.obra_id])).get(consumo.obra_id)
        return obra is not None and obra.residente_id == usuario.id
