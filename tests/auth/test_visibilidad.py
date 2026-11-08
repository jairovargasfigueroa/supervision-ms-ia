from uuid import UUID

from apoyo.tokens import GERENTE_ID, RESIDENTE_ID
from ia.auth.usuario import Usuario
from ia.auth.visibilidad import puede_ver_obra, residente_filtrado, ve_todas_las_obras

OTRO_RESIDENTE = "a0000000-0000-4000-8000-000000000004"


def test_admin_y_gerente_ven_todas() -> None:
    for rol in ("admin", "gerente"):
        usuario = Usuario(id=GERENTE_ID, roles=frozenset({rol}))
        assert ve_todas_las_obras(usuario)
        assert puede_ver_obra(usuario, OTRO_RESIDENTE)
        assert puede_ver_obra(usuario, None)
        assert residente_filtrado(usuario) is None


def test_residente_ve_solo_las_suyas() -> None:
    residente = Usuario(id=RESIDENTE_ID, roles=frozenset({"residente"}))
    assert not ve_todas_las_obras(residente)
    assert puede_ver_obra(residente, UUID(RESIDENTE_ID))
    assert puede_ver_obra(residente, RESIDENTE_ID)
    assert not puede_ver_obra(residente, OTRO_RESIDENTE)
    assert not puede_ver_obra(residente, None)
    assert residente_filtrado(residente) == RESIDENTE_ID


def test_otro_rol_no_ve_obras() -> None:
    maestro = Usuario(id=RESIDENTE_ID, roles=frozenset({"maestro"}))
    assert not puede_ver_obra(maestro, RESIDENTE_ID)
