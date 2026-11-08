"""Entorno de pruebas de los agentes (502): los cuatro servidores MCP de prueba (mismo SDK, por ASGI, con los esquemas
del contrato de la 501), MongoDB real, el `ClienteMcp` real con un Keycloak simulado y `ModeloSimulado`."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx
from pymongo.asynchronous.database import AsyncDatabase

from dobles.servidor_mcp import HerramientaPrueba, ServidorPrueba, servidores_en_marcha
from ia.agentes.aplicacion.ejecutor import EjecutorAgente, Limites
from ia.agentes.dominio.ejecucion import EN_CURSO, MANUAL, Ejecucion
from ia.agentes.infraestructura.conector_mcp import ConectorMcpReal
from ia.agentes.infraestructura.repositorio_mongo import RepositorioEjecucionesMongo, RepositorioSugerenciasMongo
from ia.comun.mongo import asegurar_indices
from ia.comun.tiempo import RelojFijo
from ia.lenguaje.aplicacion.puerto import ModeloLenguaje
from ia.mcp.cliente.catalogo import CONTRATO
from ia.mcp.cliente.token import TokenAgentes

AHORA = datetime(2027, 4, 20, 10, 0, tzinfo=UTC)  # 06:00 en La Paz
O1 = "b0000000-0000-4000-8000-000000000001"
O2 = "b0000000-0000-4000-8000-000000000002"
NOMBRE_O1 = "Edificio Las Palmas"
NOMBRE_O2 = "Condominio Sur"
CEMENTO = "e0000000-0000-4000-8000-000000000001"
COBRO = "c1000000-0000-4000-8000-000000000001"
PLANILLA = "d1000000-0000-4000-8000-000000000001"
TEXTO_DIARIO = "Lluvia fuerte: se suspendió el vaciado de la losa."

AVANCE = {
    "generadoEn": "2027-04-20T10:00:00Z",
    "obras": [
        {"obraId": O1, "nombre": NOMBRE_O1, "estado": "EN_EJECUCION", "avancePct": 32.5, "plazoPct": 45.0,
         "diferencia": 12.5, "enRiesgo": True},
        {"obraId": O2, "nombre": NOMBRE_O2, "estado": "EN_EJECUCION", "avancePct": 50.0, "plazoPct": 48.0,
         "diferencia": -2.0, "enRiesgo": False},
    ],
    "truncado": False,
}  # fmt: skip
PLANILLAS = {
    "planillas": [
        {"planillaId": PLANILLA, "obraId": O1, "obraNombre": NOMBRE_O1, "numero": 3, "periodo": "2027-03",
         "estado": "PRESENTADA", "neto": 41881.8, "observacion": None, "presentadaEn": "2027-04-01T15:00:00Z",
         "diasDesdePresentacion": 19, "cobroId": None},
    ],
    "truncado": False,
}  # fmt: skip
RIESGO = {
    "generadoEn": "2027-04-20T10:00:00Z",
    "predicciones": [
        {"obraId": O1, "obraNombre": NOMBRE_O1, "probabilidad": 0.72, "nivel": "ALTO", "factores": [],
         "versionModelo": "rr-1.0.0", "calculadaEn": "2027-04-20T05:00:00Z"},
    ],
    "total": 1,
    "truncado": False,
}  # fmt: skip
STOCK = {
    "filas": [
        {"obraId": O1, "obraNombre": NOMBRE_O1, "materialId": CEMENTO, "codigo": "CEM-IP30", "nombre": "Cemento IP-30",
         "unidad": "bolsa", "cantidad": 80.0, "minimo": 100.0, "estado": "BAJO"},
    ],
    "total": 1,
    "truncado": False,
}  # fmt: skip
ATIPICOS = {
    "consumos": [
        {"movimientoId": "f0000000-0000-4000-8000-000000000901", "obraId": O2, "obraNombre": NOMBRE_O2,
         "materialId": CEMENTO, "partidaId": "p1", "cantidad": 120.0, "periodo": "2027-04",
         "registradoEn": "2027-04-15T02:40:00Z", "puntaje": 0.71, "motivos": ["La cantidad es 4,2 veces la habitual"],
         "estadoRevision": "PENDIENTE"},
    ],
    "total": 1,
    "truncado": False,
}  # fmt: skip
COBROS = {
    "hoy": "2027-04-20",
    "cobros": [
        {"cobroId": COBRO, "numero": "COB-2027-0001", "obraId": O1, "planillaId": PLANILLA, "montoNeto": 20000.0,
         "emitidoEn": "2027-03-02", "venceEn": "2027-04-01", "estado": "VENCIDO", "diasParaVencer": -19},
    ],
    "totales": {"vencidos": {"cantidad": 1, "monto": 20000.0}, "porVencer": {"cantidad": 0, "monto": 0.0}},
    "truncado": False,
}  # fmt: skip


def diario(_: dict[str, Any]) -> dict[str, Any]:
    entrada = {"fecha": "2027-04-18", "clima": "LLUVIA_FUERTE", "texto": TEXTO_DIARIO, "truncado": False}
    return {"obraId": O1, "entradas": [entrada], "total": 1, "truncado": False}


def _h(nombre: str, respuesta: Any = None, **opciones: Any) -> HerramientaPrueba:
    esquema = CONTRATO[nombre].entrada.model_json_schema(by_alias=True)
    return HerramientaPrueba(nombre, esquema, respuesta if respuesta is not None else {}, **opciones)


def servidores_de_prueba() -> dict[str, ServidorPrueba]:
    return {
        "obras": ServidorPrueba(
            "obras", [_h("avance_obra", AVANCE), _h("buscar_diario", diario), _h("planillas_estado", PLANILLAS)]
        ),
        "materiales": ServidorPrueba(
            "materiales",
            [_h("stock_obra", STOCK), _h("consumos_partida", {"obraId": O1, "periodo": "2027-04", "filas": []})],
        ),
        "finanzas": ServidorPrueba("finanzas", [_h("cobros_por_vencer", COBROS)]),
        "ia": ServidorPrueba(
            "ia",
            [
                _h("consultar_reporte", {"conjunto": "OBRAS", "filas": [], "totalFilas": 0, "truncado": False}),
                _h("riesgo_retraso", RIESGO),
                _h("consumos_atipicos", ATIPICOS),
            ],
        ),
    }


def token_de_prueba() -> TokenAgentes:
    def keycloak(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "token-agentes", "expires_in": 300})

    return TokenAgentes(
        httpx.AsyncClient(transport=httpx.MockTransport(keycloak)), "http://kc/token", "ia-agentes", "s"
    )


@dataclass
class Entorno:
    ejecutor: EjecutorAgente
    ejecuciones: RepositorioEjecucionesMongo
    sugerencias: RepositorioSugerenciasMongo
    servidores: dict[str, ServidorPrueba]
    reloj: RelojFijo
    creadas: list[str] = field(default_factory=list)

    async def crear(self, agente: str, proveedor: str = "ollama", modelo: str = "simulado") -> Ejecucion:
        ejecucion = Ejecucion(
            id=str(uuid.uuid4()),
            agente=agente,
            origen=MANUAL,
            estado=EN_CURSO,
            solicitada_por="a0000000-0000-4000-8000-000000000002",
            iniciada_en=self.reloj.ahora(),
            proveedor_llm=proveedor,
            modelo=modelo,
        )
        await self.ejecuciones.crear(ejecucion)
        return ejecucion

    async def correr(self, agente: str) -> Ejecucion:
        """Crea la ejecución, la ejecuta y la devuelve guardada."""
        ejecucion = await self.crear(agente)
        await self.ejecutor.ejecutar(ejecucion)
        guardada = await self.ejecuciones.obtener(ejecucion.id)
        assert guardada is not None
        return guardada

    def llamadas(self) -> list[str]:
        return [h for s in self.servidores.values() for h in s.herramientas_llamadas()]


@asynccontextmanager
async def entorno(
    ia_db: AsyncDatabase[dict[str, Any]],
    modelo: ModeloLenguaje,
    limites: Limites | None = None,
    servidores: dict[str, ServidorPrueba] | None = None,
) -> AsyncIterator[Entorno]:
    await asegurar_indices(ia_db)
    servidores = servidores or servidores_de_prueba()
    conector = ConectorMcpReal(
        {n: s.url for n, s in servidores.items()},
        token_de_prueba(),
        {n: s.transporte() for n, s in servidores.items()},
    )
    reloj = RelojFijo(AHORA)
    ejecuciones = RepositorioEjecucionesMongo(ia_db)
    sugerencias = RepositorioSugerenciasMongo(ia_db)
    async with servidores_en_marcha(*servidores.values()):
        yield Entorno(
            EjecutorAgente(modelo, conector, ejecuciones, sugerencias, reloj, limites),
            ejecuciones,
            sugerencias,
            servidores,
            reloj,
        )


def evidencia(herramienta: str, dato: str = "dato de la herramienta") -> list[dict[str, str]]:
    return [{"herramienta": herramienta, "dato": dato}]


def reporte_seguimiento(**cambios: Any) -> dict[str, Any]:
    return {
        "resumen": "Una obra atrasada por lluvia y con riesgo alto.",
        "hallazgos": [
            {
                "obraId": O1,
                "tipo": "ATRASO",
                "severidad": "ALTA",
                "situacion": "Plazo 45 %, avance 32,5 %: 12,5 puntos de atraso",
                "causaProbable": "Lluvia fuerte",
                "evidencia": evidencia("avance_obra", "diferencia 12,5") + evidencia("buscar_diario", "LLUVIA_FUERTE"),
            }
        ],
        "sugerencias": [
            {"obraId": O1, "accion": "SOLICITAR_INFORME_RESIDENTE", "descripcion": "Pedir plan de recuperación",
             "hallazgo": 0},
        ],
        **cambios,
    }  # fmt: skip


def reporte_materiales() -> dict[str, Any]:
    return {
        "resumen": "Cemento bajo en Las Palmas y un consumo atípico pendiente.",
        "hallazgos": [
            {"obraId": O1, "tipo": "STOCK_BAJO", "severidad": "ALTA", "situacion": "Cemento IP-30: 80, mínimo 100",
             "causaProbable": "No determinada", "evidencia": evidencia("stock_obra", "cantidad 80, mínimo 100")},
            {"obraId": O2, "tipo": "CONSUMO_ATIPICO", "severidad": "MEDIA", "situacion": "Salida de 120 bolsas",
             "causaProbable": "No determinada", "evidencia": evidencia("consumos_atipicos", "puntaje 0,71")},
        ],
        "sugerencias": [
            {"obraId": O1, "accion": "VERIFICAR_INVENTARIO", "descripcion": "Contar el cemento", "hallazgo": 0},
            {"obraId": O2, "accion": "REVISAR_CONSUMO_ATIPICO", "descripcion": "Revisar la salida", "hallazgo": 1},
        ],
    }  # fmt: skip


def reporte_cobros() -> dict[str, Any]:
    return {
        "resumen": "Un cobro vencido y una planilla sin respuesta.",
        "hallazgos": [
            {"obraId": O1, "tipo": "COBRO_VENCIDO", "severidad": "ALTA", "situacion": "COB-2027-0001 vencido",
             "causaProbable": "No determinada", "evidencia": evidencia("cobros_por_vencer", "diasParaVencer -19")},
            {"obraId": O1, "tipo": "PLANILLA_SIN_RESPUESTA", "severidad": "MEDIA", "situacion": "Sin respuesta",
             "causaProbable": "No determinada", "evidencia": evidencia("planillas_estado", "diasDesdePresentacion 19")},
        ],
        "sugerencias": [
            {"obraId": O1, "accion": "CONTACTAR_CLIENTE_COBRO", "descripcion": "Llamar al cliente", "hallazgo": 0},
        ],
    }  # fmt: skip
