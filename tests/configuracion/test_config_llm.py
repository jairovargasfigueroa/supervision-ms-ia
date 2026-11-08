"""Configuración del modelo de lenguaje (503): proveedor, modelo por defecto y arranque sin clave."""

import pytest
from pydantic import SecretStr

from ia.config import Configuracion, ConfiguracionInvalida


def test_modelo_por_defecto_segun_el_proveedor() -> None:
    assert Configuracion(llm_proveedor="ollama", llm_modelo=None).modelo_llm == "qwen3:8b"
    anthropic = Configuracion(llm_proveedor="anthropic", llm_modelo="", anthropic_api_key=SecretStr("k"))
    assert anthropic.modelo_llm == "claude-sonnet-5-5"
    assert Configuracion(llm_proveedor="ollama", llm_modelo="llama3.1:8b").modelo_llm == "llama3.1:8b"


def test_proveedor_desconocido_no_arranca_sin_mostrar_valores() -> None:
    config = Configuracion(llm_proveedor="openai-secreto")
    with pytest.raises(ConfiguracionInvalida) as error:
        config.comprobar_llm()
    assert "openai-secreto" not in str(error.value)


def test_anthropic_sin_clave_no_arranca() -> None:
    with pytest.raises(ConfiguracionInvalida, match="IA_ANTHROPIC_API_KEY"):
        Configuracion(llm_proveedor="anthropic", anthropic_api_key=SecretStr(" ")).comprobar_llm()
    Configuracion(llm_proveedor="anthropic", anthropic_api_key=SecretStr("sk-prueba")).comprobar_llm()
    Configuracion(llm_proveedor="ollama").comprobar_llm()
