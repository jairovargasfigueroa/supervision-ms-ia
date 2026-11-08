"""Visibilidad de obras en el servicio de IA (convenciones; permisos, «IA»).

`admin` y `gerente` ven todas las obras; `residente`, las que tiene asignadas según `rpt_obra_resumen.residente_id`.
"""

from uuid import UUID

from ia.auth.usuario import Usuario


def ve_todas_las_obras(usuario: Usuario) -> bool:
    return usuario.tiene_alguno("admin", "gerente")


def puede_ver_obra(usuario: Usuario, residente_id: UUID | str | None) -> bool:
    if ve_todas_las_obras(usuario):
        return True
    if "residente" in usuario.roles and residente_id is not None:
        return str(residente_id) == usuario.id
    return False


def residente_filtrado(usuario: Usuario) -> str | None:
    """Para filtrar listas: `None` si ve todas; si no, el id que debe tener `residente_id`."""
    return None if ve_todas_las_obras(usuario) else usuario.id
