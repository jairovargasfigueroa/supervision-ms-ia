"""Configuración del servicio desde variables de entorno con prefijo `IA_` (o el archivo `.env`)."""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROVEEDORES_LLM = ("ollama", "anthropic")
MODELO_LLM_POR_DEFECTO = {"ollama": "qwen3:8b", "anthropic": "claude-sonnet-5-5"}


class ConfiguracionInvalida(Exception):
    """La configuración no permite arrancar. El mensaje nunca incluye valores de las variables (504, K2)."""


class Configuracion(BaseSettings):
    """Lo que usan `ia-api` e `ia-worker`. El secreto de `ia-reportes` no está aquí a propósito."""

    model_config = SettingsConfigDict(env_prefix="IA_", env_file=".env", extra="ignore")

    entorno: Literal["desarrollo", "produccion"] = "desarrollo"
    puerto: int = 8085

    reportes_db_url: str = "postgresql+psycopg://ia:ia@localhost:5433/reportes_db"
    mongo_url: str = "mongodb://localhost:27017"
    mongo_db: str = "ia_db"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    keycloak_issuer: str = "http://localhost:8080/realms/supervision"
    audiencia: str = "plataforma-api"
    zona_horaria: str = "America/La_Paz"

    # Modelos versionados (402): carpeta `modelos/` del repositorio; versión de `actual.txt` si no se indica.
    modelos_dir: str = "modelos"
    modelo_riesgo_version: str | None = None

    # 403: versión del modelo de consumos atípicos (si falta, la de <IA_MODELOS_DIR>/consumos_atipicos/actual.txt).
    modelo_atipicos_version: str | None = None

    obras_url: str = "http://localhost:8081"
    materiales_url: str = "http://localhost:8082"
    finanzas_url: str = "http://localhost:8083"

    # 503: usuario de solo lectura de reportes_db para el constructor de reportes.
    reportes_db_url_lectura: str = (
        "postgresql+psycopg://ia_reportes_lector:ia_reportes_lector@localhost:5433/reportes_db"
    )

    # 503: modelo de lenguaje (lo reutiliza la 502). `ollama` u `anthropic`; otro valor: el servicio no arranca.
    llm_proveedor: str = "ollama"
    # Vacío: `qwen3:8b` con Ollama y `claude-sonnet-5-5` con Anthropic.
    llm_modelo: str | None = None
    ollama_url: str = "http://localhost:11434"
    # Secreto: solo en .env o en el gestor de secretos. Con `anthropic` y sin clave, el servicio no arranca.
    anthropic_api_key: SecretStr = SecretStr("")

    # 501: hosts que acepta el servidor MCP de `ia-api` (el SDK responde 421 a otro `Host`), separados por coma.
    mcp_hosts_permitidos: str = "localhost:8085,127.0.0.1:8085"

    @property
    def hosts_mcp(self) -> list[str]:
        return [h.strip() for h in self.mcp_hosts_permitidos.split(",") if h.strip()]

    @property
    def modelo_llm(self) -> str:
        """`IA_LLM_MODELO` o el modelo por defecto del proveedor."""
        return self.llm_modelo or MODELO_LLM_POR_DEFECTO.get(self.llm_proveedor, "")

    def comprobar_llm(self) -> None:
        """Proveedor conocido y, con `anthropic`, la clave presente; si no, `ConfiguracionInvalida`."""
        if self.llm_proveedor not in PROVEEDORES_LLM:
            raise ConfiguracionInvalida("IA_LLM_PROVEEDOR debe ser «ollama» o «anthropic».")
        if self.llm_proveedor == "anthropic" and not self.anthropic_api_key.get_secret_value().strip():
            raise ConfiguracionInvalida("Con IA_LLM_PROVEEDOR=anthropic hace falta IA_ANTHROPIC_API_KEY.")

    @property
    def registros_json(self) -> bool:
        return self.entorno == "produccion"

    @property
    def jwks_url(self) -> str:
        return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/certs"

    @property
    def token_url(self) -> str:
        return f"{self.keycloak_issuer.rstrip('/')}/protocol/openid-connect/token"


class ConfiguracionCarga(Configuracion):
    """Solo la lee el comando de carga inicial: incluye el cliente confidencial `ia-reportes`."""

    reportes_client_id: str = "ia-reportes"
    reportes_client_secret: SecretStr = SecretStr("")


class ConfiguracionWorker(Configuracion):
    """Solo la lee `ia-worker` (host de los agentes, 501): incluye el cliente confidencial `ia-agentes`."""

    agentes_client_id: str = "ia-agentes"
    agentes_client_secret: SecretStr = SecretStr("")
    # Servidores MCP de la plataforma (POST /mcp de cada servicio; IA también es servidor).
    obras_mcp_url: str = "http://localhost:8081/mcp"
    materiales_mcp_url: str = "http://localhost:8082/mcp"
    finanzas_mcp_url: str = "http://localhost:8083/mcp"
    propio_mcp_url: str = "http://localhost:8085/mcp"

    # 502: ejecución diaria de los tres agentes (cron de 5 campos en la zona del negocio) y si está encendida.
    agentes_cron: str = "0 6 * * *"
    agentes_programados_activos: bool = True
