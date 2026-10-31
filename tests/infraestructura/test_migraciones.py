from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

import ia.mensajeria.inbox
import ia.reportes.infraestructura.tablas  # noqa: F401  (registran sus tablas en la metadata)
from apoyo.base_datos import configuracion_alembic
from ia.comun.base_datos import metadata
from ia.comun.mongo import IndiceMongo, asegurar_indices, indices_registrados, registrar_indice

pytestmark = pytest.mark.integracion

TABLAS_401 = {
    "rpt_obra_resumen",
    "rpt_planilla",
    "rpt_consumo_material",
    "rpt_movimiento",
    "rpt_cobro",
    "evento_aplicado",
    "carga_inicial",
}


def test_la_migracion_crea_las_tablas_y_coincide_con_las_de_core(url_migrada: str) -> None:
    motor = create_engine(url_migrada)
    try:
        with motor.connect() as conexion:
            tablas = set(inspect(conexion).get_table_names())
            assert tablas >= TABLAS_401
            columnas_planilla = {c["name"] for c in inspect(conexion).get_columns("rpt_planilla")}
            assert {"cargada_en", "veces_observada", "ultimo_origen"} <= columnas_planilla
            diferencias = compare_metadata(MigrationContext.configure(conexion), metadata)
        assert diferencias == []
    finally:
        motor.dispose()


def test_la_migracion_se_puede_revertir_y_volver_a_aplicar(url_migrada: str) -> None:
    config = configuracion_alembic(url_migrada)
    command.downgrade(config, "base")
    motor = create_engine(url_migrada)
    try:
        with motor.connect() as conexion:
            assert TABLAS_401.isdisjoint(inspect(conexion).get_table_names())
    finally:
        command.upgrade(config, "head")
        motor.dispose()


class _ColeccionFalsa:
    def __init__(self) -> None:
        self.creados: list[tuple[list[tuple[str, int]], dict[str, Any]]] = []

    async def create_index(self, claves: list[tuple[str, int]], **opciones: Any) -> str:
        self.creados.append((claves, opciones))
        return str(opciones["name"])


async def test_asegurar_indices_crea_los_registrados() -> None:
    from collections import defaultdict

    colecciones: defaultdict[str, _ColeccionFalsa] = defaultdict(_ColeccionFalsa)
    coleccion = colecciones["prueba"]
    base: Any = colecciones
    indice = IndiceMongo(coleccion="prueba", claves=(("obra_id", 1), ("fecha", -1)), nombre="ix_prueba")
    registrar_indice(indice)
    registrar_indice(indice)
    try:
        assert indices_registrados().count(indice) == 1
        await asegurar_indices(base)
        assert coleccion.creados == [([("obra_id", 1), ("fecha", -1)], {"name": "ix_prueba", "unique": False})]
    finally:
        from ia.comun import mongo

        mongo._INDICES.remove(indice)
