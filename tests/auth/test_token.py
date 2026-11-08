import socket

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from apoyo.tokens import ADMIN_ID, RESIDENTE_ID, encabezado, validador
from ia.auth.dependencias import exigir_roles
from ia.auth.token import ClavesNoDisponibles, ProveedorJwks
from ia.auth.usuario import Usuario, roles_efectivos
from ia.config import Configuracion
from ia.main import crear_app


def _cliente(caido: bool = False) -> TestClient:
    app = crear_app(Configuracion(), validador=validador(caido))

    @app.get("/solo-admin")
    async def solo_admin(usuario: Usuario = Depends(exigir_roles("admin"))) -> dict[str, str]:  # noqa: B008
        return {"id": usuario.id}

    @app.get("/todos")
    async def todos(
        usuario: Usuario = Depends(exigir_roles("admin", "gerente", "residente")),  # noqa: B008
    ) -> dict[str, object]:
        return {"id": usuario.id, "roles": sorted(usuario.roles)}

    return TestClient(app)


def test_sin_token_responde_401() -> None:
    respuesta = _cliente().get("/solo-admin")
    assert respuesta.status_code == 401
    assert respuesta.headers["content-type"] == "application/problem+json"
    assert respuesta.json()["codigo"] == "NO_AUTENTICADO"


@pytest.mark.parametrize(
    "opciones",
    [
        {"otra_clave": True},
        {"vence_en": -60},
        {"audiencia": "otra-api"},
        {"emisor": "http://otro/realms/supervision"},
    ],
    ids=["firma", "vencido", "audiencia", "emisor"],
)
def test_token_invalido_responde_401(opciones: dict[str, object]) -> None:
    respuesta = _cliente().get("/solo-admin", headers=encabezado(["admin"], **opciones))
    assert respuesta.status_code == 401
    assert respuesta.json()["codigo"] == "NO_AUTENTICADO"


def test_admin_entra_con_su_sub() -> None:
    respuesta = _cliente().get("/solo-admin", headers=encabezado(["admin"]))
    assert respuesta.status_code == 200
    assert respuesta.json() == {"id": ADMIN_ID}


def test_rol_sin_permiso_en_la_ruta_responde_403() -> None:
    respuesta = _cliente().get("/solo-admin", headers=encabezado(["gerente"]))
    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "SIN_PERMISO"


@pytest.mark.parametrize("rol", ["maestro", "almacenero", "cliente", "automatizacion", "agente_ia"])
def test_roles_que_no_atiende_el_servicio_reciben_403(rol: str) -> None:
    respuesta = _cliente().get("/todos", headers=encabezado([rol], azp=None))
    assert respuesta.status_code == 403


def test_usuario_con_varios_roles_usa_los_de_su_aplicacion() -> None:
    cliente = _cliente()
    erp = cliente.get("/todos", headers=encabezado(["residente", "cliente"], sub=RESIDENTE_ID, azp="erp"))
    portal = cliente.get("/todos", headers=encabezado(["residente", "cliente"], sub=RESIDENTE_ID, azp="portal"))
    assert erp.status_code == 200
    assert erp.json()["roles"] == ["residente"]
    assert portal.status_code == 403


def test_keycloak_sin_responder_da_503() -> None:
    respuesta = _cliente(caido=True).get("/solo-admin", headers=encabezado(["admin"]))
    assert respuesta.status_code == 503
    assert respuesta.json()["codigo"] == "SERVICIO_NO_DISPONIBLE"


def test_ruta_inexistente_404_solo_con_token_valido() -> None:
    cliente = _cliente()
    sin_token = cliente.get("/no-existe")
    con_token = cliente.get("/no-existe", headers=encabezado(["admin"]))
    assert sin_token.status_code == 401
    assert con_token.status_code == 404
    assert con_token.json()["codigo"] == "RUTA_NO_ENCONTRADA"


def test_roles_efectivos_segun_azp() -> None:
    roles = frozenset({"residente", "cliente"})
    assert roles_efectivos(roles, "portal") == {"cliente"}
    assert roles_efectivos(roles, "erp") == {"residente"}
    assert roles_efectivos(roles, "app-obra") == {"residente"}
    assert roles_efectivos(roles, "ia-reportes") == roles
    assert roles_efectivos(roles, None) == roles


def test_jwks_inalcanzable_es_claves_no_disponibles() -> None:
    with socket.socket() as libre:
        libre.bind(("127.0.0.1", 0))
        puerto = libre.getsockname()[1]
    proveedor = ProveedorJwks(f"http://127.0.0.1:{puerto}/certs", tiempo_maximo=1)
    from apoyo.tokens import token

    with pytest.raises(ClavesNoDisponibles):
        proveedor.clave_para(token(["admin"]))
