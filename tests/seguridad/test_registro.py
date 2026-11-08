"""Registro de cada llamada al modelo (504) en el puerto `ModeloLenguaje`: `ejecucionId` o `consultaId`, proveedor,
modelo, duración, tokens y validación de la salida; nunca el texto de los mensajes, tokens ni secretos."""

import json
import logging
import re
from typing import Any

import httpx
import pytest
from pymongo.asynchronous.database import AsyncDatabase

from agentes.apoyo_agentes import TEXTO_DIARIO, entorno, reporte_seguimiento
from ia.lenguaje.dominio.mensajes import Mensaje, Opciones
from ia.lenguaje.infraestructura.ollama import ModeloOllama

LINEA = re.compile(
    r"Llamada al modelo: proveedor=ollama modelo=qwen3:8b operacion=(\w+) contexto=(\S+) duracionMs=\d+ "
    r"tokensEntrada=(\S+) tokensSalida=(\S+) resultado=(\w+)"
)


def _ollama(respuestas: list[dict[str, Any]]) -> ModeloOllama:
    def manejar(_: httpx.Request) -> httpx.Response:
        cuerpo = {"model": "qwen3:8b", "created_at": "2027-04-20T10:00:00Z", "message": respuestas.pop(0), "done": True,
                  "prompt_eval_count": 50, "eval_count": 7}  # fmt: skip
        return httpx.Response(200, json=cuerpo)

    return ModeloOllama("http://ollama.prueba:11434", "qwen3:8b", transporte=httpx.MockTransport(manejar))


@pytest.mark.integracion
async def test_cada_llamada_del_agente_queda_registrada_sin_textos(
    ia_db: AsyncDatabase[dict[str, Any]], caplog: pytest.LogCaptureFixture
) -> None:
    obra = reporte_seguimiento()["hallazgos"][0]["obraId"]
    pedido = {"function": {"name": "buscar_diario", "arguments": {"obraId": obra}}}
    modelo = _ollama(
        [
            {"role": "assistant", "content": "", "tool_calls": [pedido]},
            {"role": "assistant", "content": "Listo."},
            {"role": "assistant", "content": json.dumps(reporte_seguimiento())},
        ]
    )
    with caplog.at_level(logging.DEBUG):
        async with entorno(ia_db, modelo) as e:
            ejecucion = await e.correr("SEGUIMIENTO_OBRAS")
    assert ejecucion.estado == "TERMINADA", ejecucion.error
    llamadas = [m.groups() for r in caplog.records if (m := LINEA.search(r.getMessage()))]
    assert llamadas == [
        ("conversar", ejecucion.id, "50", "7", "ok"),
        ("conversar", ejecucion.id, "50", "7", "ok"),
        ("generar_json", ejecucion.id, "50", "7", "ok"),
    ]
    validaciones = [r.getMessage() for r in caplog.records if "Validación de la salida" in r.getMessage()]
    assert validaciones == [
        f"Validación de la salida del modelo: operacion=reporte_agente contexto={ejecucion.id} valida=True"
    ]
    texto = "\n".join(r.getMessage() for r in caplog.records)
    for secreto in (TEXTO_DIARIO, "Misión:", "REGLAS", "token-agentes", "Bearer", "authorization"):
        assert secreto not in texto, secreto


async def test_un_fallo_no_previsto_tambien_se_registra(caplog: pytest.LogCaptureFixture) -> None:
    def manejar(_: httpx.Request) -> httpx.Response:
        raise RuntimeError("fallo raro")

    modelo = ModeloOllama("http://ollama.prueba:11434", "qwen3:8b", transporte=httpx.MockTransport(manejar))
    with caplog.at_level(logging.INFO), pytest.raises(RuntimeError):
        await modelo.generar_json([Mensaje.usuario("hola")], {"type": "object"}, Opciones(5, contexto="consulta-9"))
    resultados = [m.groups() for r in caplog.records if (m := LINEA.search(r.getMessage()))]
    assert resultados == [("generar_json", "consulta-9", "-", "-", "error")]
