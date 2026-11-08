"""Nombres de materiales y clientes en el motor (503): catálogo de Materiales con el token del usuario (caché de 5 min),
directorio de clientes del cuerpo, avisos y `ServicioNoDisponible`."""

from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx
import pytest

from ia.constructor_reportes.aplicacion.motor import Directorio, MotorReportes, normalizar
from ia.constructor_reportes.aplicacion.puertos import (
    ConsultaResuelta,
    FilasConsulta,
    MaterialCatalogo,
    MaterialesNoDisponibles,
    ServicioNoDisponible,
)
from ia.constructor_reportes.dominio.catalogo import MCP, REST
from ia.constructor_reportes.dominio.resultado import AVISO_CLIENTES, AVISO_MATERIALES, AVISO_PLANILLAS_SIN_PERIODO
from ia.constructor_reportes.dominio.validacion import validar_receta
from ia.constructor_reportes.infraestructura.materiales_rest import CatalogoMaterialesRest

HOY = date(2027, 3, 20)
CEMENTO = "e1"
FIERRO = "e2"
CATALOGO = {
    CEMENTO: MaterialCatalogo(CEMENTO, "CEM-IP30", "Cemento IP-30", "bolsa"),
    FIERRO: MaterialCatalogo(FIERRO, "FIE-12", "Fierro de 12 mm", "barra"),
}


class EjecutorFalso:
    def __init__(self, filas: list[tuple[Any, ...]]) -> None:
        self.filas = filas
        self.consultas: list[ConsultaResuelta] = []

    async def ejecutar(self, consulta: ConsultaResuelta) -> FilasConsulta:
        self.consultas.append(consulta)
        return FilasConsulta(consulta.receta.claves_salida(), self.filas, False)


class CatalogoFalso:
    def __init__(self, caido: bool = False) -> None:
        self.caido = caido
        self.tokens: list[str] = []

    async def materiales(self, token: str) -> Mapping[str, MaterialCatalogo]:
        self.tokens.append(token)
        if self.caido:
            raise MaterialesNoDisponibles("caído")
        return CATALOGO


def _receta(datos: dict[str, Any], modo: str = REST) -> Any:
    return validar_receta(datos, modo, HOY)


def _f(campo: str, operador: str, valor: Any) -> dict[str, Any]:
    return {"campo": campo, "operador": operador, "valor": valor}


def test_normalizar_sin_tildes_ni_mayusculas() -> None:
    assert normalizar("  Cemento PÓRTLAND ") == "cemento portland"


async def test_material_y_cliente_con_nombre_y_unidad() -> None:
    ejecutor = EjecutorFalso([("Edificio", CEMENTO, "c1", 3)])
    catalogo = CatalogoFalso()
    motor = MotorReportes(ejecutor, catalogo)
    receta = _receta(
        {
            "conjunto": "CONSUMO_MATERIAL",
            "agruparPor": ["obra", "material", "cliente"],
            "metricas": [{"funcion": "CONTEO"}],
        }
    )
    r = await motor.ejecutar(receta, HOY, directorio=Directorio({"c1": "Constructora Andina SRL"}), token="tk")
    assert [c.clave for c in r.columnas] == ["obra", "material", "unidad", "cliente", "conteo"]
    assert r.filas == [["Edificio", "Cemento IP-30", "bolsa", "Constructora Andina SRL", "3"]]
    assert r.avisos == []
    assert catalogo.tokens == ["tk"]


async def test_filtro_material_por_nombre_o_codigo_sin_tildes() -> None:
    ejecutor = EjecutorFalso([])
    motor = MotorReportes(ejecutor, CatalogoFalso())
    receta = _receta({"conjunto": "CONSUMO_MATERIAL", "filtros": [_f("material", "CONTIENE", "CEMENTÓ")]})
    await motor.ejecutar(receta, HOY, directorio=Directorio(), token="tk")
    assert ejecutor.consultas[0].ids_material == (CEMENTO,)
    receta = _receta({"conjunto": "CONSUMO_MATERIAL", "filtros": [_f("material", "CONTIENE", "fie-12")]})
    await motor.ejecutar(receta, HOY, directorio=Directorio(), token="tk")
    assert ejecutor.consultas[1].ids_material == (FIERRO,)


async def test_sin_coincidencias_vacio_sin_consultar() -> None:
    ejecutor = EjecutorFalso([("no", "debe", "salir")])
    motor = MotorReportes(ejecutor, CatalogoFalso())
    receta = _receta(
        {
            "conjunto": "CONSUMO_MATERIAL",
            "filtros": [_f("material", "CONTIENE", "Cemento'; DELETE FROM rpt_movimiento; --")],
        }
    )
    r = await motor.ejecutar(receta, HOY, directorio=Directorio(), token="tk")
    assert r.filas == [] and ejecutor.consultas == []
    receta = _receta({"conjunto": "COBROS", "filtros": [_f("cliente", "CONTIENE", "Nadie")]})
    r = await motor.ejecutar(receta, HOY, directorio=Directorio({"c1": "Andina"}), token="tk")
    assert r.filas == [] and ejecutor.consultas == []


async def test_materiales_caido_con_y_sin_filtro() -> None:
    motor = MotorReportes(EjecutorFalso([("Edificio", CEMENTO, "1", "2", "3")]), CatalogoFalso(caido=True))
    con_filtro = _receta({"conjunto": "CONSUMO_MATERIAL", "filtros": [_f("material", "CONTIENE", "cemento")]})
    with pytest.raises(ServicioNoDisponible):
        await motor.ejecutar(con_filtro, HOY, directorio=Directorio(), token="tk")
    r = await motor.ejecutar(_receta({"conjunto": "CONSUMO_MATERIAL"}), HOY, directorio=Directorio(), token="tk")
    assert r.avisos == [AVISO_MATERIALES]
    assert r.filas == [["Edificio", CEMENTO, None, "1", "2", "3"]]


async def test_sin_directorio_con_y_sin_filtro_de_cliente() -> None:
    motor = MotorReportes(EjecutorFalso([("COB-1", "c1")]), CatalogoFalso())
    with pytest.raises(ServicioNoDisponible):
        await motor.ejecutar(
            _receta({"conjunto": "COBROS", "filtros": [_f("cliente", "CONTIENE", "Andina")]}), HOY, token="tk"
        )
    r = await motor.ejecutar(_receta({"conjunto": "COBROS", "columnas": ["numero", "cliente"]}), HOY, token="tk")
    assert r.avisos == [AVISO_CLIENTES]
    assert r.filas == [["COB-1", "c1"]]


async def test_modo_mcp_devuelve_ids_sin_avisos_ni_materiales() -> None:
    catalogo = CatalogoFalso()
    motor = MotorReportes(EjecutorFalso([("Edificio", CEMENTO, "1", "2", "3")]), catalogo)
    r = await motor.ejecutar(_receta({"conjunto": "CONSUMO_MATERIAL"}, MCP), HOY)
    assert [c.clave for c in r.columnas] == ["obra", "material", "suma_cantidad", "suma_monto", "suma_salidas"]
    assert r.filas == [["Edificio", CEMENTO, "1", "2", "3"]]
    assert r.avisos == [] and catalogo.tokens == []


async def test_aviso_de_planillas_sin_periodo() -> None:
    motor = MotorReportes(EjecutorFalso([]), CatalogoFalso())
    con_observadas = _receta(
        {"conjunto": "PLANILLAS", "filtros": [_f("periodo", "IGUAL", "2027-02")], "columnas": ["obra"]}
    )
    assert (await motor.ejecutar(con_observadas, HOY, directorio=Directorio(), token="t")).avisos == [
        AVISO_PLANILLAS_SIN_PERIODO
    ]
    aprobadas = _receta(
        {
            "conjunto": "PLANILLAS",
            "filtros": [_f("periodo", "IGUAL", "2027-02"), _f("estado", "IGUAL", "APROBADA")],
            "columnas": ["obra"],
        }
    )
    assert (await motor.ejecutar(aprobadas, HOY, directorio=Directorio(), token="t")).avisos == []


# --- Adaptador REST de Materiales ---------------------------------------------------------------------------------


def _materiales(paginas: list[list[dict[str, Any]]], total: int) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    pedidos: list[httpx.Request] = []

    def manejar(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(pedido)
        pagina = int(pedido.url.params["pagina"])
        items = paginas[pagina - 1] if pagina <= len(paginas) else []
        return httpx.Response(200, json={"items": items, "total": total, "pagina": pagina, "tamano": 100})

    return httpx.MockTransport(manejar), pedidos


def _item(i: int) -> dict[str, Any]:
    return {"id": f"m{i}", "codigo": f"C-{i}", "nombre": f"Material {i}", "unidad": "u", "precioReferencial": 1.0}


async def test_catalogo_todas_las_paginas_con_token_y_cache() -> None:
    transporte, pedidos = _materiales([[_item(i) for i in range(100)], [_item(100)]], total=101)
    ahora = [0.0]
    catalogo = CatalogoMaterialesRest("http://materiales.prueba", transporte, reloj=lambda: ahora[0])
    materiales = await catalogo.materiales("token-del-usuario")
    assert len(materiales) == 101 and materiales["m100"].nombre == "Material 100"
    assert [p.url.params["pagina"] for p in pedidos] == ["1", "2"]
    assert all(p.url.params["tamano"] == "100" for p in pedidos)
    assert pedidos[0].headers["authorization"] == "Bearer token-del-usuario"
    ahora[0] = 299
    await catalogo.materiales("otro")
    assert len(pedidos) == 2  # caché de 5 minutos
    ahora[0] = 301
    await catalogo.materiales("otro")
    assert len(pedidos) == 4


async def test_catalogo_caido_o_con_error() -> None:
    def caido(pedido: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no", request=pedido)

    with pytest.raises(MaterialesNoDisponibles):
        await CatalogoMaterialesRest("http://m", httpx.MockTransport(caido)).materiales("t")
    with pytest.raises(MaterialesNoDisponibles):
        await CatalogoMaterialesRest("http://m", httpx.MockTransport(lambda _: httpx.Response(503))).materiales("t")
