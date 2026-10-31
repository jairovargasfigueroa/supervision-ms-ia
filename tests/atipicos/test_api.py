"""REST de consumos atípicos con la API real (ASGI), PostgreSQL y MongoDB reales y tokens firmados en la prueba.

403, diseño «REST del servicio de IA» y «Pruebas» (IA, API): visibilidad por rol, filtros y revisión.
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.asynchronous.database import AsyncDatabase
from sqlalchemy.ext.asyncio import AsyncEngine

from apoyo.eventos import CEMENTO, OBRA_1, OBRA_2, PARTIDA_102, Eventos, obra_registrada
from apoyo.tokens import ADMIN_ID, GERENTE_ID, RESIDENTE_ID, encabezado, validador
from ia.atipicos.dominio.consumo import ConsumoAtipico
from ia.atipicos.infraestructura.repositorio_mongo import COLECCION, RepositorioAtipicosMongo
from ia.comun.tiempo import RelojFijo
from ia.config import Configuracion
from ia.main import crear_app

pytestmark = pytest.mark.integracion

OTRO_RESIDENTE = "a0000000-0000-4000-8000-000000000099"
SIN_OBRAS = "a0000000-0000-4000-8000-000000000098"
OBRA_SIN_REPORTES = "b0000000-0000-4000-8000-000000000777"
REVISADO_EN = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)

BASE = ConsumoAtipico(
    id="f0000000-0000-4000-8000-000000000001",
    obra_id=OBRA_1,
    material_id=CEMENTO,
    partida_id=PARTIDA_102,
    cantidad=Decimal("120"),
    periodo="2026-11",
    registrado_en=datetime(2026, 11, 15, 2, 40, tzinfo=UTC),
    puntaje=0.71,
    motivos=(
        "La cantidad es 4,2 veces la habitual de este material en la obra",
        "Se registró fuera del horario habitual (22:40)",
    ),
    variables={"log_cantidad": 4.8},
    version_modelo="ca-1.0.0",
    detectado_en=datetime(2026, 11, 15, 2, 40, 3, tzinfo=UTC),
)


def consumo(n: int, **cambios: Any) -> ConsumoAtipico:
    return replace(BASE, id=f"f0000000-0000-4000-8000-{n:012d}", **cambios)


@pytest.fixture
async def cliente(
    motor: AsyncEngine, url_mongo_pruebas: str, ia_db: AsyncDatabase[dict[str, Any]]
) -> AsyncIterator[httpx.AsyncClient]:
    eventos = Eventos(motor)
    await eventos.publicar("ObraRegistrada", obra_registrada(OBRA_1))  # residente RESIDENTE_ID
    otra = obra_registrada(OBRA_2)
    otra["asignaciones"] = {**otra["asignaciones"], "residenteId": OTRO_RESIDENTE}
    await eventos.publicar("ObraRegistrada", otra)

    config = Configuracion(mongo_url=url_mongo_pruebas, mongo_db=ia_db.name)
    app: FastAPI = crear_app(config, validador=validador(), motor=motor)
    app.state.reloj = RelojFijo(REVISADO_EN)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ia") as cliente,
    ):
        yield cliente


@pytest.fixture
def repo(ia_db: AsyncDatabase[dict[str, Any]]) -> RepositorioAtipicosMongo:
    return RepositorioAtipicosMongo(ia_db)


async def guardar(repo: RepositorioAtipicosMongo, *consumos: ConsumoAtipico) -> None:
    for c in consumos:
        assert await repo.guardar_si_no_existe(c)


# GET /consumos-atipicos


async def test_lista_con_el_contrato_del_gateway(cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo) -> None:
    await guardar(repo, BASE)
    respuesta = await cliente.get(f"/consumos-atipicos?obraId={OBRA_1}", headers=encabezado(["admin"]))
    assert respuesta.status_code == 200
    assert respuesta.json() == [
        {
            "id": BASE.id,
            "movimientoId": BASE.id,
            "obraId": OBRA_1,
            "materialId": CEMENTO,
            "partidaId": PARTIDA_102,
            "cantidad": 120.0,
            "periodo": "2026-11",
            "registradoEn": "2026-11-15T02:40:00Z",
            "puntaje": 0.71,
            "motivos": list(BASE.motivos),
            "estadoRevision": "PENDIENTE",
            "revisadoPor": None,
            "revisadoEn": None,
            "comentario": None,
            "versionModelo": "ca-1.0.0",
            "detectadoEn": "2026-11-15T02:40:03Z",
        }
    ]


async def test_de_la_mas_reciente_a_la_mas_antigua_y_maximo_200(
    cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo, ia_db: AsyncDatabase[dict[str, Any]]
) -> None:
    from ia.atipicos.infraestructura.repositorio_mongo import a_documento

    inicio = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
    await ia_db[COLECCION].insert_many(
        [a_documento(consumo(n, registrado_en=inicio + timedelta(hours=n))) for n in range(1, 206)]
    )
    respuesta = await cliente.get("/consumos-atipicos", headers=encabezado(["gerente"], sub=GERENTE_ID))
    assert respuesta.status_code == 200
    fechas = [c["registradoEn"] for c in respuesta.json()]
    assert len(fechas) == 200
    assert fechas == sorted(fechas, reverse=True)
    assert fechas[0] == (inicio + timedelta(hours=205)).isoformat().replace("+00:00", "Z")


async def test_visibilidad_por_rol(cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo) -> None:
    await guardar(repo, consumo(1), consumo(2, obra_id=OBRA_2))
    residente = encabezado(["residente"], sub=RESIDENTE_ID)

    sin_obra = await cliente.get("/consumos-atipicos", headers=residente)
    assert [c["obraId"] for c in sin_obra.json()] == [OBRA_1]
    otra = await cliente.get(f"/consumos-atipicos?obraId={OBRA_2}", headers=residente)
    assert otra.status_code == 404
    assert otra.json()["codigo"] == "OBRA_NO_ENCONTRADA"

    for roles, sub in ((["admin"], ADMIN_ID), (["gerente"], GERENTE_ID)):
        todas = await cliente.get("/consumos-atipicos", headers=encabezado(roles, sub=sub))
        assert sorted(c["obraId"] for c in todas.json()) == sorted([OBRA_1, OBRA_2])
        de_otra = await cliente.get(f"/consumos-atipicos?obraId={OBRA_2}", headers=encabezado(roles, sub=sub))
        assert [c["obraId"] for c in de_otra.json()] == [OBRA_2]

    sin_obras = await cliente.get("/consumos-atipicos", headers=encabezado(["residente"], sub=SIN_OBRAS))
    assert sin_obras.json() == []
    maestro = await cliente.get("/consumos-atipicos", headers=encabezado(["maestro"]))
    assert maestro.status_code == 403
    assert (await cliente.get("/consumos-atipicos")).status_code == 401


async def test_obra_que_no_existe_da_404(cliente: httpx.AsyncClient) -> None:
    for obra in (OBRA_SIN_REPORTES, "no-es-uuid"):
        respuesta = await cliente.get(f"/consumos-atipicos?obraId={obra}", headers=encabezado(["admin"]))
        assert respuesta.status_code == 404
        assert respuesta.json()["codigo"] == "OBRA_NO_ENCONTRADA"


async def test_filtros_por_estado_y_fechas(cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo) -> None:
    await guardar(
        repo,
        # 14/11 23:30 en La Paz (15/11 03:30 UTC), 15/11 10:00 y 16/11 10:00 en La Paz.
        consumo(1, registrado_en=datetime(2026, 11, 15, 3, 30, tzinfo=UTC)),
        consumo(2, registrado_en=datetime(2026, 11, 15, 14, 0, tzinfo=UTC), estado_revision="NORMAL"),
        consumo(3, registrado_en=datetime(2026, 11, 16, 14, 0, tzinfo=UTC), estado_revision="CONFIRMADO"),
    )
    admin = encabezado(["admin"])

    async def ids(consulta: str) -> list[str]:
        respuesta = await cliente.get(f"/consumos-atipicos?obraId={OBRA_1}{consulta}", headers=admin)
        assert respuesta.status_code == 200, respuesta.text
        return [c["id"][-1] for c in respuesta.json()]

    assert await ids("") == ["3", "2", "1"]  # sin estado, todas las revisiones
    assert await ids("&estado=PENDIENTE") == ["1"]
    assert await ids("&estado=NORMAL") == ["2"]
    assert await ids("&desde=2026-11-15") == ["3", "2"]
    assert await ids("&hasta=2026-11-15") == ["2", "1"]
    assert await ids("&desde=2026-11-15&hasta=2026-11-15") == ["2"]


@pytest.mark.parametrize(
    "consulta",
    [
        "estado=REVISADO",
        "estado=pendiente",
        "desde=15-11-2026",
        "hasta=2026-13-01",
        "desde=2026-11-16&hasta=2026-11-15",
    ],
)
async def test_estado_o_fechas_invalidos_dan_400(cliente: httpx.AsyncClient, consulta: str) -> None:
    respuesta = await cliente.get(f"/consumos-atipicos?obraId={OBRA_1}&{consulta}", headers=encabezado(["admin"]))
    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "DATOS_INVALIDOS"


# GET /consumos-atipicos/resumen


async def test_resumen_de_pendientes_por_obra(cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo) -> None:
    await guardar(repo, consumo(1), consumo(2), consumo(3, estado_revision="NORMAL"))
    consulta = f"/consumos-atipicos/resumen?obraIds={OBRA_1},{OBRA_2},{OBRA_SIN_REPORTES},otra"

    admin = await cliente.get(consulta, headers=encabezado(["admin"]))
    assert admin.status_code == 200
    assert admin.json() == [{"obraId": OBRA_1, "pendientes": 2}, {"obraId": OBRA_2, "pendientes": 0}]

    residente = await cliente.get(consulta, headers=encabezado(["residente"], sub=RESIDENTE_ID))
    assert residente.json() == [{"obraId": OBRA_1, "pendientes": 2}]
    otro = await cliente.get(consulta, headers=encabezado(["residente"], sub=OTRO_RESIDENTE))
    assert otro.json() == [{"obraId": OBRA_2, "pendientes": 0}]


async def test_resumen_de_mas_de_100_obras_da_400(cliente: httpx.AsyncClient) -> None:
    obras = ",".join(f"b0000000-0000-4000-8000-{n:012d}" for n in range(101))
    respuesta = await cliente.get(f"/consumos-atipicos/resumen?obraIds={obras}", headers=encabezado(["admin"]))
    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "DEMASIADAS_OBRAS"
    cien = ",".join(f"b0000000-0000-4000-8000-{n:012d}" for n in range(100))
    assert (
        await cliente.get(f"/consumos-atipicos/resumen?obraIds={cien}", headers=encabezado(["admin"]))
    ).status_code == 200


# PUT /consumos-atipicos/{id}/revision


async def test_revision_correcta_guarda_autor_fecha_y_comentario(
    cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo
) -> None:
    await guardar(repo, BASE)
    respuesta = await cliente.put(
        f"/consumos-atipicos/{BASE.id}/revision",
        json={"estado": "CONFIRMADO", "comentario": "  Se usó en la losa de otra obra sin registrarlo "},
        headers=encabezado(["residente"], sub=RESIDENTE_ID),
    )
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["estadoRevision"] == "CONFIRMADO"
    assert cuerpo["revisadoPor"] == RESIDENTE_ID
    assert cuerpo["revisadoEn"] == "2026-11-15T18:00:00Z"
    assert cuerpo["comentario"] == "Se usó en la losa de otra obra sin registrarlo"
    guardado = await repo.obtener(BASE.id)
    assert guardado is not None
    assert (guardado.estado_revision, guardado.revisado_por, guardado.revisado_en) == (
        "CONFIRMADO",
        RESIDENTE_ID,
        REVISADO_EN,
    )


async def test_es_normal_admite_sin_comentario(cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo) -> None:
    await guardar(repo, BASE)
    respuesta = await cliente.put(
        f"/consumos-atipicos/{BASE.id}/revision", json={"estado": "NORMAL"}, headers=encabezado(["admin"])
    )
    assert respuesta.status_code == 200
    assert (respuesta.json()["estadoRevision"], respuesta.json()["comentario"]) == ("NORMAL", None)
    assert respuesta.json()["revisadoPor"] == ADMIN_ID


@pytest.mark.parametrize(
    ("cuerpo", "regla"),
    [
        ({"estado": "CONFIRMADO"}, "El comentario es obligatorio al confirmar el problema."),
        ({"estado": "CONFIRMADO", "comentario": "   "}, "El comentario es obligatorio al confirmar el problema."),
        ({"estado": "CONFIRMADO", "comentario": "Malo"}, "El comentario debe tener entre 5 y 300 caracteres."),
        ({"estado": "NORMAL", "comentario": "x" * 301}, "El comentario debe tener entre 5 y 300 caracteres."),
    ],
)
async def test_comentario_invalido_da_422(
    cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo, cuerpo: dict[str, str], regla: str
) -> None:
    await guardar(repo, BASE)
    respuesta = await cliente.put(f"/consumos-atipicos/{BASE.id}/revision", json=cuerpo, headers=encabezado(["admin"]))
    assert respuesta.status_code == 422
    assert respuesta.json()["codigo"] == "COMENTARIO_INVALIDO"
    assert respuesta.json()["errores"] == [{"campo": "comentario", "mensaje": regla}]
    assert (await repo.obtener(BASE.id)).estado_revision == "PENDIENTE"  # type: ignore[union-attr]


async def test_segunda_revision_da_409(cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo) -> None:
    await guardar(repo, BASE)
    url = f"/consumos-atipicos/{BASE.id}/revision"
    assert (await cliente.put(url, json={"estado": "NORMAL"}, headers=encabezado(["admin"]))).status_code == 200
    otra = await cliente.put(
        url, json={"estado": "CONFIRMADO", "comentario": "Sí era un problema"}, headers=encabezado(["admin"])
    )
    assert otra.status_code == 409
    assert otra.json()["codigo"] == "REVISION_YA_REGISTRADA"
    assert (await repo.obtener(BASE.id)).estado_revision == "NORMAL"  # type: ignore[union-attr]


async def test_gerente_recibe_403(cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo) -> None:
    await guardar(repo, BASE)
    respuesta = await cliente.put(
        f"/consumos-atipicos/{BASE.id}/revision",
        json={"estado": "NORMAL"},
        headers=encabezado(["gerente"], sub=GERENTE_ID),
    )
    assert respuesta.status_code == 403
    assert respuesta.json()["codigo"] == "SIN_PERMISO"


async def test_no_existe_o_residente_de_otra_obra_da_404(
    cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo
) -> None:
    await guardar(repo, consumo(2, obra_id=OBRA_2))
    for id_, encabezados in (
        ("f0000000-0000-4000-8000-000000000404", encabezado(["admin"])),
        (consumo(2).id, encabezado(["residente"], sub=RESIDENTE_ID)),
        (consumo(2).id, encabezado(["residente", "gerente"], sub=RESIDENTE_ID)),
    ):
        respuesta = await cliente.put(
            f"/consumos-atipicos/{id_}/revision", json={"estado": "NORMAL"}, headers=encabezados
        )
        assert respuesta.status_code == 404
        assert respuesta.json()["codigo"] == "CONSUMO_ATIPICO_NO_ENCONTRADO"
    assert (await repo.obtener(consumo(2).id)).estado_revision == "PENDIENTE"  # type: ignore[union-attr]


@pytest.mark.parametrize("cuerpo", [{"estado": "PENDIENTE"}, {"estado": "OTRO"}, {}, {"comentario": "Sin estado"}])
async def test_estado_pendiente_o_desconocido_da_400(
    cliente: httpx.AsyncClient, repo: RepositorioAtipicosMongo, cuerpo: dict[str, str]
) -> None:
    await guardar(repo, BASE)
    respuesta = await cliente.put(f"/consumos-atipicos/{BASE.id}/revision", json=cuerpo, headers=encabezado(["admin"]))
    assert respuesta.status_code == 400
    assert respuesta.json()["codigo"] == "DATOS_INVALIDOS"
