"""Adaptadores Ollama y Anthropic con transporte HTTP simulado (nunca un modelo real).

Se comprueba la traducción de mensajes y herramientas, los ids de las llamadas, el esquema adaptado y el mapeo de
errores a `LlmNoDisponible`, `LlmTiempoAgotado` y `SalidaNoValida`.
"""

import asyncio
import json
import logging
from typing import Any

import httpx
import httpx2
import pytest
from pydantic import SecretStr

from ia.comun.registros import silenciar_librerias
from ia.config import Configuracion, ConfiguracionInvalida
from ia.lenguaje.aplicacion.puerto import LlmNoDisponible, LlmTiempoAgotado, SalidaNoValida
from ia.lenguaje.dominio.mensajes import DefinicionHerramienta, LlamadaHerramienta, Mensaje, Opciones, Rol
from ia.lenguaje.infraestructura.anthropic import ModeloAnthropic
from ia.lenguaje.infraestructura.componer import crear_modelo_lenguaje
from ia.lenguaje.infraestructura.esquemas import adaptar_esquema
from ia.lenguaje.infraestructura.ollama import ModeloOllama

OPCIONES = Opciones(tiempo_maximo_s=5, temperatura=0, esfuerzo="BAJO", contexto="consulta-1")
ESQUEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "resumen": {"type": "string", "minLength": 1, "maxLength": 1000},
        "filas": {"type": "integer", "minimum": 0, "maximum": 100},
        "lista": {"type": "array", "minItems": 2, "items": {"type": "object", "properties": {"x": {"type": "string"}}}},
    },
    "required": ["resumen"],
}
HERRAMIENTA = DefinicionHerramienta(
    "stock_obra", "Devuelve el stock.", {"type": "object", "properties": {"limite": {"type": "integer", "maximum": 9}}}
)


def test_adaptar_esquema_anthropic_quita_restricciones_y_cierra_objetos() -> None:
    adaptado = adaptar_esquema(ESQUEMA, "anthropic")
    assert adaptado["additionalProperties"] is False
    assert adaptado["properties"]["resumen"] == {"type": "string"}
    assert adaptado["properties"]["filas"] == {"type": "integer"}
    assert "minItems" not in adaptado["properties"]["lista"]
    assert adaptado["properties"]["lista"]["items"]["additionalProperties"] is False
    assert ESQUEMA["properties"]["resumen"]["minLength"] == 1  # el original no cambia
    assert adaptar_esquema(ESQUEMA, "ollama") == ESQUEMA


# --- Ollama -------------------------------------------------------------------------------------------------------


def _ollama(responder: Any) -> tuple[ModeloOllama, list[dict[str, Any]]]:
    pedidos: list[dict[str, Any]] = []

    def manejar(pedido: httpx.Request) -> httpx.Response:
        assert pedido.url.path == "/api/chat"
        pedidos.append(json.loads(pedido.content))
        return responder(pedido)

    return ModeloOllama("http://ollama.prueba:11434", "qwen3:8b", transporte=httpx.MockTransport(manejar)), pedidos


def _respuesta_ollama(contenido: str = "", llamadas: list[dict[str, Any]] | None = None) -> httpx.Response:
    mensaje: dict[str, Any] = {"role": "assistant", "content": contenido}
    if llamadas:
        mensaje["tool_calls"] = llamadas
    return httpx.Response(
        200,
        json={
            "model": "qwen3:8b",
            "created_at": "2026-10-31T12:00:00Z",
            "message": mensaje,
            "done": True,
            "prompt_eval_count": 120,
            "eval_count": 30,
        },
    )


async def test_ollama_generar_json_envia_el_esquema_y_las_opciones() -> None:
    modelo, pedidos = _ollama(lambda _: _respuesta_ollama('{"resumen": "Hay 3 obras."}'))
    datos = await modelo.generar_json([Mensaje.sistema("reglas"), Mensaje.usuario("pregunta")], ESQUEMA, OPCIONES)
    assert datos == {"resumen": "Hay 3 obras."}
    pedido = pedidos[0]
    assert pedido["model"] == "qwen3:8b"
    assert pedido["format"] == ESQUEMA
    assert pedido["think"] is False
    assert pedido["options"] == {"num_ctx": 16384, "temperature": 0}
    assert [m["role"] for m in pedido["messages"]] == ["system", "user"]


async def test_ollama_conversar_pone_ids_a_las_llamadas_y_devuelve_resultados() -> None:
    llamadas = [{"function": {"name": "stock_obra", "arguments": {"limite": 3}}}]
    modelo, pedidos = _ollama(lambda _: _respuesta_ollama("", llamadas))
    previa = LlamadaHerramienta("h1", "avance_obra", {})
    historial = [
        Mensaje.usuario("hola"),
        Mensaje(Rol.ASISTENTE, "", llamadas=(previa,)),
        Mensaje.resultado(previa, '{"obras": []}'),
    ]
    respuesta = await modelo.conversar(historial, [HERRAMIENTA], OPCIONES)
    assert respuesta.llamadas == (LlamadaHerramienta("h2", "stock_obra", {"limite": 3}),)
    assert respuesta.tokens_entrada == 120
    pedido = pedidos[0]
    assert pedido["tools"][0]["function"]["name"] == "stock_obra"
    assert pedido["tools"][0]["function"]["parameters"]["properties"]["limite"]["type"] == "integer"
    assert pedido["messages"][1]["tool_calls"] == [{"function": {"name": "avance_obra", "arguments": {}}}]
    assert pedido["messages"][2] == {"role": "tool", "content": '{"obras": []}', "tool_name": "avance_obra"}


@pytest.mark.parametrize("estado", [500, 503, 429, 404])
async def test_ollama_errores_del_servidor_son_llm_no_disponible(estado: int) -> None:
    modelo, _ = _ollama(lambda _: httpx.Response(estado, json={"error": "fallo"}))
    with pytest.raises(LlmNoDisponible):
        await modelo.generar_json([Mensaje.usuario("x")], ESQUEMA, OPCIONES)


async def test_ollama_sin_conexion_tiempo_y_salida_no_valida() -> None:
    def caido(pedido: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin conexión", request=pedido)

    modelo, _ = _ollama(caido)
    with pytest.raises(LlmNoDisponible):
        await modelo.generar_json([Mensaje.usuario("x")], ESQUEMA, OPCIONES)

    def lento(pedido: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=pedido)

    modelo, _ = _ollama(lento)
    with pytest.raises(LlmTiempoAgotado):
        await modelo.generar_json([Mensaje.usuario("x")], ESQUEMA, OPCIONES)

    modelo, _ = _ollama(lambda _: _respuesta_ollama("esto no es JSON"))
    with pytest.raises(SalidaNoValida):
        await modelo.generar_json([Mensaje.usuario("x")], ESQUEMA, OPCIONES)
    modelo, _ = _ollama(lambda _: _respuesta_ollama("[1, 2]"))
    with pytest.raises(SalidaNoValida):
        await modelo.generar_json([Mensaje.usuario("x")], ESQUEMA, OPCIONES)


async def test_ollama_vence_el_tiempo_maximo() -> None:
    async def manejar(pedido: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return _respuesta_ollama("{}")

    modelo = ModeloOllama("http://ollama.prueba:11434", "qwen3:8b", transporte=httpx.MockTransport(manejar))
    with pytest.raises(LlmTiempoAgotado):
        await modelo.generar_json([Mensaje.usuario("x")], ESQUEMA, Opciones(tiempo_maximo_s=0.05))


# --- Anthropic ----------------------------------------------------------------------------------------------------

CLAVE = "sk-ant-prueba-no-real"


def _anthropic(responder: Any) -> tuple[ModeloAnthropic, list[httpx2.Request]]:
    pedidos: list[httpx2.Request] = []

    def manejar(pedido: httpx2.Request) -> httpx2.Response:
        assert pedido.url.path == "/v1/messages"
        pedidos.append(pedido)
        return responder(pedido)

    return ModeloAnthropic(CLAVE, "claude-sonnet-5-5", transporte=httpx2.MockTransport(manejar)), pedidos


def _respuesta_anthropic(bloques: list[dict[str, Any]], stop: str = "end_turn") -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5-5",
            "content": bloques,
            "stop_reason": stop,
            "stop_sequence": None,
            "usage": {"input_tokens": 200, "output_tokens": 40},
        },
    )


async def test_anthropic_generar_json_con_salida_estructurada_y_esfuerzo() -> None:
    modelo, pedidos = _anthropic(lambda _: _respuesta_anthropic([{"type": "text", "text": '{"resumen": "Bien."}'}]))
    datos = await modelo.generar_json([Mensaje.sistema("reglas"), Mensaje.usuario("pregunta")], ESQUEMA, OPCIONES)
    assert datos == {"resumen": "Bien."}
    cuerpo = json.loads(pedidos[0].content)
    assert cuerpo["model"] == "claude-sonnet-5-5"
    assert cuerpo["system"] == "reglas"
    assert cuerpo["messages"] == [{"role": "user", "content": [{"type": "text", "text": "pregunta"}]}]
    assert cuerpo["thinking"] == {"type": "between_tools"}
    assert cuerpo["output_config"]["effort"] == "low"
    assert cuerpo["output_config"]["format"] == {"type": "json_schema", "schema": adaptar_esquema(ESQUEMA, "anthropic")}
    assert "temperature" not in cuerpo
    assert pedidos[0].headers["authorization"] == f"Bearer {CLAVE}"


async def test_anthropic_conversar_con_herramientas_estrictas_y_resultados() -> None:
    bloques = [
        {"type": "thinking", "thinking": "pienso", "signature": "firma"},
        {"type": "tool_use", "id": "toolu_1", "name": "stock_obra", "input": {"limite": 3}},
    ]
    modelo, pedidos = _anthropic(lambda _: _respuesta_anthropic(bloques, stop="tool_use"))
    opciones = Opciones(tiempo_maximo_s=5, esfuerzo="MEDIO")
    primera = await modelo.conversar([Mensaje.usuario("revisa")], [HERRAMIENTA], opciones)
    assert primera.llamadas == (LlamadaHerramienta("toolu_1", "stock_obra", {"limite": 3}),)
    cuerpo = json.loads(pedidos[0].content)
    assert cuerpo["tools"] == [
        {
            "name": "stock_obra",
            "description": "Devuelve el stock.",
            "input_schema": adaptar_esquema(HERRAMIENTA.esquema, "anthropic"),
            "strict": True,
        }
    ]
    assert cuerpo["output_config"] == {"effort": "medium"}

    # Segunda vuelta: el asistente con sus bloques crudos (incluido el razonamiento) y el resultado como tool_result.
    historial = [
        Mensaje.usuario("revisa"),
        primera.mensaje,
        Mensaje.resultado(primera.llamadas[0], '{"filas": []}'),
        Mensaje.resultado(LlamadaHerramienta("toolu_2", "otra", {}), "fallo", es_error=True),
    ]
    await modelo.conversar(historial, [HERRAMIENTA], opciones)
    mensajes = json.loads(pedidos[1].content)["messages"]
    assert mensajes[1]["role"] == "assistant"
    assert mensajes[1]["content"][0]["type"] == "thinking"
    assert mensajes[1]["content"][0]["signature"] == "firma"
    assert mensajes[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"filas": []}', "is_error": False},
            {"type": "tool_result", "tool_use_id": "toolu_2", "content": "fallo", "is_error": True},
        ],
    }


@pytest.mark.parametrize(("estado", "tipo"), [(500, "api_error"), (529, "overloaded_error"), (429, "rate_limit_error")])
async def test_anthropic_errores_son_llm_no_disponible(estado: int, tipo: str) -> None:
    modelo, _ = _anthropic(
        lambda _: httpx2.Response(estado, json={"type": "error", "error": {"type": tipo, "message": "x"}})
    )
    with pytest.raises(LlmNoDisponible):
        await modelo.generar_json([Mensaje.usuario("x")], ESQUEMA, OPCIONES)


async def test_anthropic_sin_conexion_y_salida_no_valida() -> None:
    def caido(pedido: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("sin conexión", request=pedido)

    modelo, _ = _anthropic(caido)
    with pytest.raises(LlmNoDisponible):
        await modelo.generar_json([Mensaje.usuario("x")], ESQUEMA, OPCIONES)

    modelo, _ = _anthropic(lambda _: _respuesta_anthropic([{"type": "text", "text": "no es JSON"}]))
    with pytest.raises(SalidaNoValida):
        await modelo.generar_json([Mensaje.usuario("x")], ESQUEMA, OPCIONES)


async def test_anthropic_tiempo_agotado() -> None:
    def lento(pedido: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("lento", request=pedido)

    modelo, _ = _anthropic(lento)
    with pytest.raises(LlmTiempoAgotado):
        await modelo.generar_json([Mensaje.usuario("x")], ESQUEMA, OPCIONES)


async def test_registro_sin_clave_ni_texto(caplog: pytest.LogCaptureFixture) -> None:
    """Con las librerías silenciadas como en `configurar_registros` (el SDK registra el cuerpo en DEBUG)."""
    modelo, _ = _anthropic(lambda _: _respuesta_anthropic([{"type": "text", "text": "{}"}]))
    silenciar_librerias()
    with caplog.at_level(logging.DEBUG):
        await modelo.generar_json([Mensaje.usuario("pregunta secreta del usuario")], ESQUEMA, OPCIONES)
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "proveedor=anthropic" in texto
    assert "contexto=consulta-1" in texto
    assert "tokensEntrada=200" in texto
    assert CLAVE not in texto
    assert "pregunta secreta" not in texto


def test_crear_modelo_lenguaje_segun_proveedor() -> None:
    assert crear_modelo_lenguaje(Configuracion(llm_proveedor="ollama")).proveedor == "ollama"
    anthropic = crear_modelo_lenguaje(Configuracion(llm_proveedor="anthropic", anthropic_api_key=SecretStr("k")))
    assert (anthropic.proveedor, anthropic.modelo) == ("anthropic", "claude-sonnet-5-5")
    with pytest.raises(ConfiguracionInvalida):
        crear_modelo_lenguaje(Configuracion(llm_proveedor="anthropic"))
    with pytest.raises(ConfiguracionInvalida):
        crear_modelo_lenguaje(Configuracion(llm_proveedor="otro"))
