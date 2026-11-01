"""Ejecuta las consultas del constructor con el usuario de solo lectura `ia_reportes_lector`.

Conexión aparte (`IA_REPORTES_DB_URL_LECTURA`), transacción `READ ONLY` (opción de la conexión de psycopg) y
`SET LOCAL statement_timeout = '5s'` (texto constante, el único `text()` del constructor). Un tiempo vencido o una
base caída → `ReportesNoDisponibles`.
"""

import logging
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ia.constructor_reportes.aplicacion.puertos import (
    ConsultaResuelta,
    EjecutorConsultas,
    FilasConsulta,
    ReportesNoDisponibles,
)
from ia.constructor_reportes.infraestructura.constructor_sql import (
    construir_select,
    seleccion_nombres_obras,
    seleccion_obras_filtradas,
    seleccion_obras_por_nombre,
)

registro = logging.getLogger("ia.constructor_reportes")

TIEMPO_MAXIMO = text("SET LOCAL statement_timeout = '5s'")


class EjecutorSql:
    def __init__(self, motor_lector: AsyncEngine) -> None:
        self._motor = motor_lector

    async def ejecutar(self, consulta: ConsultaResuelta) -> FilasConsulta:
        receta = consulta.receta
        filas = await self.filas(construir_select(consulta))
        claves = receta.claves_salida()
        return FilasConsulta(claves, [tuple(f) for f in filas[: receta.limite]], len(filas) > receta.limite)

    async def obras_filtradas(self, consulta: ConsultaResuelta) -> list[str] | None:
        seleccion = seleccion_obras_filtradas(consulta)
        if seleccion is None:
            return None
        return [str(f[0]) for f in await self.filas(seleccion)]

    async def nombres_obras(self, obra_ids: Sequence[str]) -> dict[str, str]:
        if not obra_ids:
            return {}
        return {str(f[0]): str(f[1]) for f in await self.filas(seleccion_nombres_obras(obra_ids))}

    async def obras_por_nombre(self) -> list[str]:
        return [str(f[0]) for f in await self.filas(seleccion_obras_por_nombre())]

    async def filas(self, consulta: Select[Any]) -> Sequence[Any]:
        try:
            async with self._motor.connect() as conexion:
                conexion = await conexion.execution_options(postgresql_readonly=True)
                async with conexion.begin():
                    await conexion.execute(TIEMPO_MAXIMO)
                    filas: Sequence[Any] = (await conexion.execute(consulta)).all()
                    return filas
        except SQLAlchemyError as error:
            registro.warning("La base de reportes no respondió: %s", type(error).__name__)
            raise ReportesNoDisponibles("La base de reportes no respondió.") from error
        except OSError as error:
            raise ReportesNoDisponibles("La base de reportes no respondió.") from error


class EjecutorReportes:
    """SQL para los cinco conjuntos de la base de reportes y MongoDB para `CONSUMOS_ATIPICOS`."""

    def __init__(self, sql: EjecutorSql, atipicos: EjecutorConsultas) -> None:
        self._sql = sql
        self._atipicos = atipicos

    async def ejecutar(self, consulta: ConsultaResuelta) -> FilasConsulta:
        if consulta.receta.conjunto == "CONSUMOS_ATIPICOS":
            return await self._atipicos.ejecutar(consulta)
        return await self._sql.ejecutar(consulta)
