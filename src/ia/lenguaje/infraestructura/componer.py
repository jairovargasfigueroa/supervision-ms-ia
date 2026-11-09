"""Elige el adaptador del modelo de lenguaje según `IA_LLM_PROVEEDOR`."""

from ia.config import Configuracion
from ia.lenguaje.aplicacion.puerto import ModeloLenguaje
from ia.lenguaje.aplicacion.reintentos import ModeloConReintentos
from ia.lenguaje.infraestructura.anthropic import ModeloAnthropic
from ia.lenguaje.infraestructura.ollama import ModeloOllama


def crear_modelo_lenguaje(config: Configuracion) -> ModeloLenguaje:
    """`ConfiguracionInvalida` con un proveedor desconocido o con `anthropic` sin clave (el servicio no arranca).
    El adaptador va envuelto en `ModeloConReintentos` (505)."""
    config.comprobar_llm()
    adaptador: ModeloLenguaje
    if config.llm_proveedor == "anthropic":
        adaptador = ModeloAnthropic(config.anthropic_api_key.get_secret_value(), config.modelo_llm)
    else:
        adaptador = ModeloOllama(config.ollama_url, config.modelo_llm)
    return ModeloConReintentos(adaptador)
