"""`consulta_reporte` y `limite_consulta` en MongoDB real (503)."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from ia.comun.mongo import asegurar_indices
from ia.constructor_reportes.aplicacion.puertos import ConsultaGuardada
from ia.constructor_reportes.infraestructura.consultas_mongo import RepositorioConsultasMongo
from ia.constructor_reportes.infraestructura.limite_mongo import LimitadorConsultasMongo

pytestmark = pytest.mark.integracion

AHORA = datetime(2027, 3, 20, 14, 0, 45, tzinfo=UTC)


def _consulta(i: int, usuario: str = "u1") -> ConsultaGuardada:
    return ConsultaGuardada(
        id=f"c{i}",
        usuario_id=usuario,
        texto=f"pedido {i}",
        atajo=None,
        conjunto="OBRAS",
        receta={"conjunto": "OBRAS", "limite": 100},
        resumen="r",
        resumen_plantilla=False,
        resultado="OK",
        error_codigo=None,
        total_filas=i,
        proveedor_llm="ollama",
        modelo="qwen3:8b",
        duracion_ms=10,
        creada_en=AHORA + timedelta(seconds=i),
    )


async def test_consultas_del_usuario_mas_recientes_primero(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    repositorio = RepositorioConsultasMongo(ia_db)
    for i in range(3):
        await repositorio.guardar(_consulta(i))
    await repositorio.guardar(_consulta(9, usuario="otro"))
    ultimas = await repositorio.ultimas("u1", 2)
    assert [c.id for c in ultimas] == ["c2", "c1"]
    assert ultimas[0] == _consulta(2)


async def test_limite_por_minuto_atomico_con_dos_procesos(
    ia_db: AsyncDatabase[dict[str, Any]], url_mongo_pruebas: str
) -> None:
    otro_cliente: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(url_mongo_pruebas, tz_aware=True)
    try:
        proceso_a = LimitadorConsultasMongo(ia_db)
        proceso_b = LimitadorConsultasMongo(otro_cliente[ia_db.name])
        resultados = await asyncio.gather(*[(proceso_a if i % 2 else proceso_b).contar("u1", AHORA) for i in range(12)])
        assert sorted(cuenta for cuenta, _ in resultados) == list(range(1, 13))
        assert {segundos for _, segundos in resultados} == {15}
        # Otro minuto, otra cuenta; otro usuario, otra cuenta.
        assert (await proceso_a.contar("u1", AHORA + timedelta(minutes=1)))[0] == 1
        assert (await proceso_a.contar("u2", AHORA))[0] == 1
        documento = await ia_db["limite_consulta"].find_one({"_id": "u1:2027-03-20T14:00"})
        assert documento is not None
        assert documento["expira_en"] == datetime(2027, 3, 20, 14, 3, tzinfo=UTC)
    finally:
        await otro_cliente.close()


async def test_indice_ttl_del_limite(ia_db: AsyncDatabase[dict[str, Any]]) -> None:
    await asegurar_indices(ia_db)
    indices = await ia_db["limite_consulta"].index_information()
    assert indices["ttl_limite_consulta_expira"]["expireAfterSeconds"] == 0
    assert "ix_consulta_reporte_usuario_creada" in await ia_db["consulta_reporte"].index_information()
