from fastapi import FastAPI
from fastapi.testclient import TestClient

from ia.comun.errores import ErrorApi
from ia.comun.http import instalar_http
from ia.comun.registros import id_peticion
from ia.config import Configuracion
from ia.main import crear_app


def _app_con_rutas_de_prueba() -> FastAPI:
    app = FastAPI()
    instalar_http(app)

    @app.get("/conflicto")
    async def conflicto() -> None:
        raise ErrorApi(estado=409, codigo="ALGO_EN_CONFLICTO", titulo="Conflicto", detalle="Detalle.")

    @app.get("/numero/{valor}")
    async def numero(valor: int) -> dict[str, int]:
        return {"valor": valor}

    @app.get("/falla")
    async def falla() -> None:
        raise RuntimeError("secreto interno")

    @app.get("/request-id")
    async def request_id() -> dict[str, str | None]:
        return {"requestId": id_peticion.get()}

    return app


def test_salud_responde_sin_token() -> None:
    cliente = TestClient(crear_app(Configuracion()))
    respuesta = cliente.get("/salud")
    assert respuesta.status_code == 200
    assert respuesta.json() == {"estado": "ok"}


def test_request_id_se_reenvia_y_queda_en_el_contexto() -> None:
    cliente = TestClient(_app_con_rutas_de_prueba())
    respuesta = cliente.get("/request-id", headers={"X-Request-Id": "abc-123"})
    assert respuesta.headers["x-request-id"] == "abc-123"
    assert respuesta.json() == {"requestId": "abc-123"}


def test_request_id_se_crea_si_no_viene_o_no_es_valido() -> None:
    cliente = TestClient(_app_con_rutas_de_prueba())
    sin = cliente.get("/request-id")
    invalido = cliente.get("/request-id", headers={"X-Request-Id": "con espacios"})
    for respuesta in (sin, invalido):
        creado = respuesta.headers["x-request-id"]
        assert len(creado) == 36
        assert respuesta.json() == {"requestId": creado}


def test_encabezados_de_seguridad() -> None:
    respuesta = TestClient(crear_app(Configuracion())).get("/salud")
    assert respuesta.headers["x-content-type-options"] == "nosniff"
    assert respuesta.headers["referrer-policy"] == "no-referrer"


def test_error_propio_en_problem_details() -> None:
    respuesta = TestClient(_app_con_rutas_de_prueba()).get("/conflicto")
    assert respuesta.status_code == 409
    assert respuesta.headers["content-type"] == "application/problem+json"
    assert respuesta.json() == {
        "type": "about:blank",
        "title": "Conflicto",
        "status": 409,
        "codigo": "ALGO_EN_CONFLICTO",
        "detail": "Detalle.",
    }


def test_validacion_responde_400_con_errores_por_campo() -> None:
    respuesta = TestClient(_app_con_rutas_de_prueba()).get("/numero/abc")
    assert respuesta.status_code == 400
    cuerpo = respuesta.json()
    assert cuerpo["codigo"] == "DATOS_INVALIDOS"
    assert cuerpo["errores"][0]["campo"] == "valor"


def test_ruta_inexistente_sin_autenticador_responde_404() -> None:
    respuesta = TestClient(_app_con_rutas_de_prueba()).get("/no-existe")
    assert respuesta.status_code == 404
    assert respuesta.json()["codigo"] == "RUTA_NO_ENCONTRADA"


def test_error_inesperado_responde_500_sin_detalles_internos() -> None:
    cliente = TestClient(_app_con_rutas_de_prueba(), raise_server_exceptions=False)
    respuesta = cliente.get("/falla")
    assert respuesta.status_code == 500
    assert respuesta.headers["content-type"] == "application/problem+json"
    assert respuesta.json()["codigo"] == "ERROR_INTERNO"
    assert "secreto" not in respuesta.text
